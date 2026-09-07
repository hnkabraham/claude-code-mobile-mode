#!/usr/bin/env python3
"""UserPromptSubmit hook: attach mobile-mode guidance to this one turn.

This is the Claude Code analogue of what Happy (github.com/slopus/happy) does
in `packages/happy-app/sources/sync/sync.ts` -- it attaches an
`appendSystemPrompt` to the *message* rather than the session, so the
instruction only rides along on turns that actually came from a phone.

We can't detect origin the way Happy can (nothing in the hook's stdin JSON or
the process env marks a local session as being driven by Remote Control), so
the switch is explicit and scoped to the session id that arrives on stdin.
Other sessions on the same machine never see it.

Silence is the safe default: any unexpected condition prints `{}` and the turn
proceeds untouched.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_mode_state as state  # noqa: E402

TOGGLE_COMMAND = "/mobile-mode:toggle"

PUSH_ITEMS = {
    "always": """\
2. PUSH ONE LINE EVERY TURN. If the PushNotification tool is available, call
   it once per turn, every turn -- the user has opted in to a push for every
   reply, short answers included, and that overrides the tool's own "err
   toward not sending" default. Send it right before you wait on a question,
   or as the last thing before the turn ends. Lead with the thing itself:
   "auth tests failing, 2 of 14" beats "task complete", and for a plain answer
   put the answer in the line. Do not push again if you are continuing after
   a stop-hook nudge.""",
    "needed": """\
2. PUSH ONE LINE WHEN THEY NEED TO LOOK. If the PushNotification tool is
   available, call it once per turn: right before you wait on a question, or
   when the turn ends with something they would act on. Lead with the thing
   itself -- "auth tests failing, 2 of 14" beats "task complete". Do not push
   again if you are continuing after a stop-hook nudge.""",
    "never": """\
2. DO NOT PUSH. Never call PushNotification in this session, even if the tool
   is offered: the user has turned pushes off for mobile mode and is notified
   another way.""",
}

PUSH_TAILS = {
    "always": "; push only if something changed that they would want to know about.",
    "needed": "; push only if something changed that they would want to know about.",
    "never": " and do not push.",
}

END_CLAUSES = {
    False: (
        "A turn that finishes the work should\n"
        "   just end. Never manufacture a question: one whose answer would not\n"
        "   change what you do next is worse than none."
    ),
    True: (
        "Never manufacture a decision that isn't\n"
        "   real. But when the work is finished and nothing needs the user's input,\n"
        "   still end by calling AskUserQuestion with 2-4 concrete next steps,\n"
        "   phrased as prompts they could pick up next, so they tap instead of\n"
        "   type. Make them distinct and genuinely useful -- a suggestion they\n"
        "   would never choose is noise."
    ),
}

GUIDANCE_TEMPLATE = """\
<mobile-mode>
Mobile mode is on for this session: the user is driving it from their phone,
away from the terminal. Reading is cheap for them, typing is expensive, tapping
is free.

1. ASK ONLY WHEN THE NEXT ACTION NEEDS THEIR DECISION -- and when it does, ask
   with AskUserQuestion so the choices render as taps instead of prose they
   would have to answer by thumb-typing. Keep each option to a few words and
   make the options genuinely different. {end_clause}

{push_item}

3. WRITE FOR A PHONE SCREEN. Lead with the answer. Cut the preamble. Long
   tables and wide code blocks do not survive the trip.

If this turn was started by a scheduled wakeup or a background task rather
than by the user, skip the question{push_tail}
</mobile-mode>"""


def guidance(push: str = state.DEFAULT_PUSH, suggest: bool = False) -> str:
    """The guidance text for one push cadence and suggestion preference."""
    if push not in PUSH_ITEMS:
        push = state.DEFAULT_PUSH
    return GUIDANCE_TEMPLATE.format(
        end_clause=END_CLAUSES[bool(suggest)],
        push_item=PUSH_ITEMS[push],
        push_tail=PUSH_TAILS[push],
    )


GUIDANCE = guidance()  # the default cadence + no suggestions, kept under its old name

RETRACTION = """\
<mobile-mode>
Mobile mode is now OFF for this session. Earlier <mobile-mode> guidance in
this conversation no longer applies: do not end turns with AskUserQuestion or
PushNotification unless the user asks for them, and write normally for the
terminal again.
</mobile-mode>"""


def emit(context: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))


def decide(event: dict) -> str:
    """Return the context to inject for this event, or "" for nothing."""
    prompt = event.get("prompt")
    if isinstance(prompt, str) and prompt.lstrip().startswith(TOGGLE_COMMAND):
        return ""  # the switch itself never gets the guidance

    session_id = event.get("session_id")
    record = state.load(session_id)
    if record.get("mode") in ("on", "enforce"):
        return guidance(state.push_cadence(record), state.suggest_on(record))

    if record.get("retract_pending"):
        # One-shot: clear the flag first so a retraction cannot repeat. Any
        # remembered preference (push, suggest) keeps the record alive so it
        # survives the retraction; with none to keep, drop the record.
        try:
            prefs = {k: record[k] for k in ("push", "suggest") if k in record}
            if prefs:
                state.save(session_id, {"mode": "off", **prefs})
            else:
                state.remove(session_id)
        except OSError:
            try:
                state.save(session_id, {"mode": "off"})
            except OSError:
                pass
        return RETRACTION

    return ""


def main() -> None:
    try:
        # Drain stdin first, unconditionally: the hook contract expects it, and
        # leaving it unread can surface as a broken pipe on the writing side.
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            event = {}
        context = decide(event)
        if context:
            emit(context)
        else:
            print("{}")
    except Exception:
        # Never let this hook break a turn. A missing nudge is a small loss; a
        # hook that errors on every prompt is a broken session.
        print("{}")


if __name__ == "__main__":
    main()

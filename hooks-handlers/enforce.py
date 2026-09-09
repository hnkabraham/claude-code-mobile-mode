#!/usr/bin/env python3
"""Stop hook: optionally refuse to end a mobile-mode turn that fell short.

OFF BY DEFAULT. Only acts when this session's mode is "enforce"
(`/mobile-mode:toggle enforce`). The guidance injected by inject.py is usually
enough; this exists for when it isn't.

Checks two independent things, either of which can block a turn:
  - did it offer the user anything to tap (AskUserQuestion)?
  - if the push cadence is "always", did it call PushNotification?
"needed"/"never" cadence is not enforced here: whether a push was "needed" is
a judgment call the Stop hook has no way to verify, so nagging about it would
mostly be wrong. "always" is closer to unambiguous -- it means every turn --
but it is not a guarantee: a session where PushNotification is not offered at
all (no Remote Control, push disabled in /config) gets exactly one unwinnable
nudge per turn, which is why PUSH_REASON explicitly excuses that case rather
than looping. Nor can a Stop hook ever verify a push sent *before* an
AskUserQuestion, as the guidance asks for on cadence "always"/"needed" --
by the time Stop fires, the question has already been asked and answered, so
this only catches a push missing from the whole turn, not a late one.

Happy could only ask nicely -- it had a system prompt and no way to check the
result. A Stop hook can actually verify, at the cost of being able to nag about
a turn that had nothing worth asking (or nothing worth pushing). That trade is
why this is opt-in, and why it is best-effort: blocking happens at most once
per turn (Claude Code sets `stop_hook_active` on the retry, and we always let
that through), and any uncertainty about the transcript resolves to "let the
turn end".
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_mode_state as state  # noqa: E402

ASK_REASON = (
    "Mobile mode is on and this turn ended without offering the user anything to "
    "tap. If there is a real next step or a decision that is theirs to make, call "
    "AskUserQuestion with those as options now. If the work is genuinely finished "
    "and there is nothing worth asking, say so in one short line and stop -- do "
    "not invent a question."
)
REASON = ASK_REASON  # old name, kept for anything still importing it

PUSH_REASON = (
    "Mobile mode's push cadence is \"always\" and this turn ended without calling "
    "PushNotification. If that tool is offered in this session, call it now with "
    "a one-line status before the turn ends -- the user is away from the "
    "terminal and relies on the push to know a reply happened. If the tool is "
    "not offered here, there is nothing more to do: say so in one short line "
    "and stop -- this one nudge is enough, do not keep trying."
)


def _tool_used_since_boundary(transcript_path: str, tool_name: str):
    """Did the assistant call ``tool_name`` since the last real user message?

    Returns True when the call happened (and was not rejected or cancelled),
    False when the turn is visible and made no such call, and None when it
    cannot tell -- unreadable, empty, or no user message found at all. None
    must never block: absence of evidence is not grounds to refuse a stop.

    Walks the transcript backwards and stops at the first user message that is
    actual user input. Tool results also arrive with role "user", so those are
    skipped -- treating one as the turn boundary would truncate the window and
    make us miss a tool call that did happen. Tool results are also how we
    learn a call failed: an `is_error` result for a matching call (rejected,
    cancelled, timed out) means it did not actually take effect.
    """
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None

    failed_ids = set()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue

        message = entry.get("message")
        if not isinstance(message, dict):
            message = {}
        role = message.get("role") or entry.get("type")
        content = message.get("content")

        if role == "user":
            if isinstance(content, str):
                return False  # plain text: this is the turn boundary
            if isinstance(content, list):
                results = [
                    b for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_result"
                ]
                if not results:
                    return False  # text blocks: also the boundary
                for result in results:
                    if result.get("is_error"):
                        failed_ids.add(result.get("tool_use_id"))
            continue

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") == tool_name
                    and block.get("id") not in failed_ids
                ):
                    return True

    return None  # never found a user message: nothing to judge


def offered_options(transcript_path: str):
    """Did the assistant call AskUserQuestion since the last real user message?"""
    return _tool_used_since_boundary(transcript_path, "AskUserQuestion")


def sent_push(transcript_path: str):
    """Did the assistant call PushNotification since the last real user message?"""
    return _tool_used_since_boundary(transcript_path, "PushNotification")


def main() -> None:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
        if not isinstance(event, dict):
            event = {}

        if event.get("stop_hook_active"):
            print("{}")  # we already nudged once this turn; never loop
            return

        session_id = event.get("session_id")
        record = state.load(session_id)
        if record.get("mode") != "enforce":
            print("{}")
            return

        if record.get("skip_next_stop"):
            # The turn that switched enforcement on (or checked status) has
            # nothing to ask. Consume the pass; the next turn is fair game.
            # Keep any push/suggest preference -- this is a pass on the check,
            # not a reset of what the user configured.
            try:
                prefs = {k: record[k] for k in ("push", "suggest") if k in record}
                state.save(session_id, {"mode": "enforce", **prefs})
            except OSError:
                pass
            print("{}")
            return

        transcript = event.get("transcript_path")
        if not transcript:
            print("{}")
            return

        reasons = []
        if offered_options(transcript) is False:
            reasons.append(ASK_REASON)
        if state.push_cadence(record) == "always" and sent_push(transcript) is False:
            reasons.append(PUSH_REASON)

        if not reasons:
            print("{}")
            return

        print(json.dumps({"decision": "block", "reason": " ".join(reasons)}))
    except Exception:
        print("{}")


if __name__ == "__main__":
    main()

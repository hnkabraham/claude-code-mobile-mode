---
name: mobile-mode
description: Set up, explain, troubleshoot, or change what mobile mode injects — the plugin that makes Claude Code end turns with tappable AskUserQuestion options and a push notification when the user is driving the session from their phone via Remote Control. Use for "mobile mode isn't firing", "stop asking me questions on my phone", "change the mobile prompt", "why didn't I get a notification".
---

# mobile-mode

Makes a phone-driven Claude Code session end each turn with something tappable
instead of something to type. Scoped to one session: the one you toggle it in.

## How it works

Two hooks, one launcher, and a per-session state file. Nothing runs unless
`~/.claude/mobile-mode/sessions/<session-id>.json` exists for the session.

| File | Event | Job |
|---|---|---|
| `run.sh` | — | finds a working Python 3 (`python3`, then `py -3`, then `python`) and runs one handler under it |
| `hooks-handlers/inject.py` | `UserPromptSubmit` | returns `hookSpecificOutput.additionalContext` with the guidance, per turn |
| `hooks-handlers/enforce.py` | `Stop` | opt-in; blocks a turn that ended without `AskUserQuestion`, at most once per turn |
| `hooks-handlers/toggle.py` | — | backend for `/mobile-mode:toggle`; writes the session's state file |
| `hooks-handlers/mobile_mode_state.py` | — | shared: where state lives, validation, atomic writes, pruning |

Both hooks read the session id from their stdin JSON. The slash command gets the
same id from Claude Code's `${CLAUDE_SESSION_ID}` substitution and passes it to
`toggle.py`. That is the whole scoping mechanism: other sessions on the same
machine — cron-driven, chat bridges, other terminals — never match and never see
any of this.

Guidance is injected per *message*, not per session, so it costs nothing on
turns where mobile mode is off, and it can be flipped mid-session.

## Switching it

`/mobile-mode:toggle on | off | enforce | relax | status`. Transitions are
absolute: `on` always means guidance-only (it clears `enforce`), `enforce` always
means guidance + Stop-hook blocking, `relax` is the same as `on`, `off` clears
everything. Takes effect on the next turn — no restart.

The push cadence is a preference on top of the mode, stored as `push` in the
same record: `always` (default) asks for one push every turn, `needed` only
when the turn ends with something to act on, `never` tells the model not to
push at all. Set it with a second word (`on needed`, `enforce always`) or on
its own with `push <cadence>`, which also turns the mode on if it was off. It
is remembered for the rest of the session, across mode changes and `off` — a
record with mode `off` may linger just to carry it. Each cadence swaps item 2
of the guidance; the rest is identical.

Suggestions are a second preference, stored as `suggest` (a bool, default off).
With `suggest on`, item 1 of the guidance changes: instead of "a finished turn
just ends", the model is told to end even a completed turn with an
`AskUserQuestion` of 2–4 next-step prompts to tap (a real decision still comes
first when one exists). Set it with `suggest on` / `suggest off`; like the push
cadence it turns the mode on if it was off and is remembered across mode
changes and `off`. `enforce` composes naturally with it — a turn that always
offers suggestions never trips the Stop hook.

Turning it `off` also queues a one-time retraction for the next turn, telling
the model that the earlier `<mobile-mode>` guidance still sitting in the
conversation no longer applies.

The toggle's own turn is exempt on both sides: it never receives the guidance,
and whenever it leaves the mode at `enforce` it hands the Stop hook one free
pass (`skip_next_stop` in the state file), so switching enforcement on is not
the first thing enforcement nags about.

## Changing what it says

Edit `GUIDANCE` (or `RETRACTION`) in `hooks-handlers/inject.py`. Plain strings,
no build step, no restart. Test it with:

```bash
echo '{"session_id": "<id>", "prompt": "hi"}' | sh "<plugin root>/run.sh" inject
```

With that session off it prints `{}`. With it on it prints the JSON the hook
feeds to the model. `<plugin root>` is `~/.claude/plugins/cache/<marketplace>/mobile-mode/<version>`
for a marketplace install, or `~/.claude/skills/mobile-mode` for a skills-dir
install.

## Troubleshooting

**Nothing is being injected.** `/mobile-mode:toggle status` first — the state
file is the whole gate, and it is per session, so a mode set in another terminal
does not carry over. Then run the test command above. If the toggle reports "no
usable session id", Claude Code is older than 2.1.9 and does not substitute
`${CLAUDE_SESSION_ID}` yet.

**Hooks error with "Python was not found" or "no Python 3.8+ found".** On
Windows `python3` is often the Microsoft Store stub. `run.sh` already skips it
and tries `py -3` and `python`; if none of those is a real Python 3.8+, install
one. Windows also needs Git Bash, which is the shell Claude Code uses for hooks
there anyway.

**No push on the phone.** The push half is the `PushNotification` tool, which
Claude Code only offers when Remote Control is active and push is enabled in
`/config` → Notifications. The guidance says "if the tool is available", so a
missing push on the desktop is expected, not a fault.

**Too many questions.** That is the guidance working as written but the model
reading it too eagerly. Soften item 1 in `GUIDANCE` rather than turning the
whole thing off.

**Enforce nags a turn that had nothing to ask.** By design it can — the Stop
hook cannot tell "lazy" from "finished". It blocks at most once per turn and
the reason it sends tells the model to say "nothing to ask" in one line. If
that is still too much, `relax`.

## What it deliberately does not do

It does not detect whether a prompt actually came from a phone. Nothing in the
hook's stdin JSON or the process environment marks a local session as being
driven by Remote Control — `CLAUDE_CODE_REMOTE*` refers to cloud sessions, which
is a different thing. Hence the explicit, per-session switch.

It cannot tell your own turns apart from a scheduled wakeup or background task
firing *inside the same session* you toggled. Those turns get the guidance too.
The guidance tells the model to skip the question on such turns and push only
if something changed; in `enforce` mode each one costs at most one extra
"nothing to ask" round-trip.

## Prior art

The design is lifted from Happy (github.com/slopus/happy), which did the same
thing for Claude Code before Remote Control existed. It injected its instruction
per-message as `meta.appendSystemPrompt` (`sync.ts:842`), had the model emit an
`<options>` XML block, and regexed it back out client-side to render buttons
(`parseMarkdownBlock.ts:117`). `AskUserQuestion` replaces the XML round-trip —
it renders as tappable chips natively, so there is nothing to parse.

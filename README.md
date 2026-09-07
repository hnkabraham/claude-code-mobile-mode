# mobile-mode

A Claude Code plugin for people who drive sessions from their phone.

When you're on mobile, reading is cheap, typing is expensive, and tapping is free.
Claude Code doesn't know that. `mobile-mode` tells it — per turn, behind a
per-session switch, so nothing changes when you're back at the terminal and
nothing changes in the *other* sessions running on the same machine.

With it on, a turn ends with something you can tap:

- **Tappable options.** When the next step is genuinely your call, it goes out as
  `AskUserQuestion`, which renders as chips in Remote Control, instead of as a
  paragraph you'd have to answer by thumb-typing.
- **A push every turn.** One line, leading with the result, so nothing lands
  silently while you're away from the screen. Claude Code drops it on its own
  when the terminal is active, so it costs nothing at the desk.
- **Output written for a phone screen.** Answer first, no preamble.

## Install

```bash
/plugin marketplace add holystreetballer/claude-code-mobile-mode
/plugin install mobile-mode
```

Or clone straight into your skills directory, which loads it as a plugin
(hooks included) on the next session:

```bash
git clone https://github.com/holystreetballer/claude-code-mobile-mode ~/.claude/skills/mobile-mode
```

Requires Claude Code 2.1.157 or newer, a Python 3.8+ somewhere on `PATH`
(stdlib only — no dependencies), and a POSIX `sh`. On Windows that means Git
Bash, which Claude Code already uses to run hooks; the launcher also knows that
`python3` there is usually the Microsoft Store stub and tries `py -3` and
`python` instead.

## Use

```
/mobile-mode:toggle on        # guidance only — the sane default
/mobile-mode:toggle enforce   # also let the Stop hook block an optionless turn
/mobile-mode:toggle relax     # back to guidance only (same as `on`)
/mobile-mode:toggle off
/mobile-mode:toggle status
```

Flip it on when you pick up your phone, off when you sit back down. It applies
to **the session you run it in** and no other. Hooks read the switch at fire
time, so it takes effect on the next turn — no restart. Transitions are
absolute: `on` always means guidance-only, even if `enforce` was set before.

## How it works

Two hooks, a launcher, and one small state file per session.

| | Event | Job |
|---|---|---|
| `run.sh` | — | finds a working Python 3 and runs one handler under it |
| `hooks-handlers/inject.py` | `UserPromptSubmit` | returns `hookSpecificOutput.additionalContext` carrying the mobile guidance |
| `hooks-handlers/enforce.py` | `Stop` | *opt-in*; blocks a turn that ended without `AskUserQuestion`, at most once per turn |
| `hooks-handlers/toggle.py` | — | backend for `/mobile-mode:toggle` |

The guidance is attached to each **message**, not to the session. That's the whole
trick: it costs nothing on turns where mobile mode is off, and it can be flipped
mid-session without restarting anything.

Everything is gated on `~/.claude/mobile-mode/sessions/<session-id>.json`. The
slash command learns the session id from Claude Code's `${CLAUDE_SESSION_ID}`
substitution; the hooks get the same id on stdin. Every failure path — no state,
unreadable transcript, malformed stdin — prints `{}` and lets the turn through
untouched. A hook that breaks your session is worse than a missing nudge.

Turning it `off` also hands the model a one-line retraction on the next turn, so
the guidance already sitting in the conversation stops applying instead of
quietly lingering.

## Why a per-session switch instead of detecting your phone

Because it can't be detected. A hook receives no indication of where a prompt came
from: there's no origin field in its stdin JSON, and no environment marker for a
local session being driven by Remote Control (`CLAUDE_CODE_REMOTE*` refers to cloud
sessions, which is a different thing).

And a machine-wide switch would be worse than nothing. The moment you flipped it
on from your phone, every unattended session on that box — a chat-channel bridge,
a cron job, a scheduled monitoring loop — would start firing push notifications
at nobody and ending its turns with questions nobody will tap. Keying the switch
on the session id means only the session you toggled changes.

## On enforcement

`enforce` is off by default, and it should probably stay that way.

The Stop hook can verify that a turn offered options, but it cannot tell *why* one
didn't. "The model got lazy" and "the work is genuinely finished" look identical
from the outside. Try the guidance alone first; reach for `enforce` only if the
prompt proves too loose in practice.

It is best-effort by construction: it blocks at most once per turn (Claude Code
marks the retry with `stop_hook_active`, which always passes), a cancelled or
timed-out question does not count as one, the toggle's own turn gets a free
pass, and any doubt about the transcript — missing, empty, unreadable —
resolves to letting the turn end.

The guidance itself is deliberately hedged: it tells the model that a question
whose answer wouldn't change what it does next is worse than no question at all.
Turn-ending options are only worth anything when they represent a real decision.

## Known limit

A scheduled wakeup or background task that fires *inside the session you
toggled* is indistinguishable from you. Those turns get the guidance too. The
guidance tells the model to skip the question on such turns and push only when
something changed; in `enforce` mode each one costs at most one extra
"nothing to ask" round-trip. If you run long unattended loops, run them in
their own session and leave mobile mode off there.

## Prior art

[Happy](https://github.com/slopus/happy) did this for Claude Code before Remote
Control existed, and the per-message design here is lifted from it: Happy attaches
`meta.appendSystemPrompt` to every message the phone sends (`sync.ts:842`), has the
model emit an `<options>` XML block, then regexes it back out client-side to render
buttons (`parseMarkdownBlock.ts:117`).

The one change is `AskUserQuestion` in place of the XML round-trip. Happy had to
invent a tag and parse it because it controls its own client; a plugin doesn't, and
`AskUserQuestion` already renders as tappable chips with nothing to parse.

## Development

```bash
python -m pytest -q
```

The suite runs the handlers the way Claude Code does — through `sh run.sh` and
through the exact command strings in `hooks/hooks.json` — so it needs a POSIX
`sh` on `PATH` (Git Bash on Windows).

## License

MIT

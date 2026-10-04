# mobile-mode

A Claude Code plugin for people who drive sessions from their phone.

When you're on mobile, reading is cheap, typing is expensive, and tapping is free.
Claude Code doesn't know that. `mobile-mode` tells it — per turn, only on turns
that came from your phone, so nothing changes when you're back at the terminal and
nothing changes in the *other* sessions running on the same machine.

**New in 0.6.0:** on Claude Code builds with function hooks ("mods"), it detects
Remote Control prompts by itself — no toggle needed — and sends the push itself
when the turn ends instead of asking the model to remember. Older builds keep the
per-session toggle described below.

**New in 0.7.0** (function-hook builds):

- **Approval pushes.** When a permission dialog opens during a phone turn, you get
  "Needs your OK: Bash — rm -rf build/" right away instead of at the end of the turn.
- **Quick-action buttons** under the latest reply in the Claude mobile app
  (default: Continue / Simpler / Next?). A tap submits that prompt as you.
- **Background pushes.** A turn started by a finished background task pushes when
  it ends, if you used the phone in the last 3 hours.
- **Long-turn push.** One "Still working (10 min): <last step>" push on a long phone turn.
- **Quiet hours** (default 23-7): no routine pushes overnight; approval requests and
  failed turns still push.

**New in 0.8.0 — next-prompt suggestions.** After a phone turn, a small model
(Haiku by default, through your session's own login) reads your last message and
the reply and proposes 2-3 likely next prompts. They show as buttons under the reply
in the mobile app, in place of the fixed quick actions, until the next turn; if the
call fails, the fixed buttons come back. Happy got options by asking the main model
to end each reply with an `<options>` block; a separate small call keeps the reply
clean and adds nothing to the main turn. `suggestions: always` also puts the first
one in the terminal prompt box as the Tab suggestion.

Settings (Claude Code's config menu, or `pluginConfigs` → `mobile-mode` in settings):
`quietHours` (`23-7`, or `off`), `quickActions` (`Label=prompt|Label=prompt`, empty
hides them), `longTurnMinutes` (`10`, `0` = off), `suggestions` (`phone` | `always` |
`off`), `suggestModel` (`haiku`).

With it on, a turn ends with something you can tap:

- **Tappable options.** When the next step is genuinely your call, it goes out as
  `AskUserQuestion`, which renders as chips in Remote Control, instead of as a
  paragraph you'd have to answer by thumb-typing.
- **A push every turn**, or only when something needs you: your call. One line,
  leading with the result, so nothing lands silently while you're away from the
  screen. Claude Code drops it on its own when the terminal is active, so it
  costs nothing at the desk.
- **Output written for a phone screen.** Answer first, no preamble.

## Install

```bash
/plugin marketplace add hnkabraham/claude-code-mobile-mode
/plugin install mobile-mode
```

Or clone straight into your skills directory, which loads it as a plugin
(hooks included) on the next session:

```bash
git clone https://github.com/hnkabraham/claude-code-mobile-mode ~/.claude/skills/mobile-mode
```

Requires Claude Code 2.1.157 or newer, a Python 3.8+ somewhere on `PATH`
(stdlib only — no dependencies), and a POSIX `sh`. On Windows that means Git
Bash, which Claude Code already uses to run hooks; the launcher also knows that
`python3` there is usually the Microsoft Store stub and tries `py -3` and
`python` instead.

## Use

```
/mobile-mode:toggle on        # guidance only — the sane default
/mobile-mode:toggle enforce   # also let the Stop hook block an optionless or push-skipping turn
/mobile-mode:toggle relax     # back to guidance only (same as `on`)
/mobile-mode:toggle off
/mobile-mode:toggle status

/mobile-mode:toggle on needed    # push only when the turn ends with something to act on
/mobile-mode:toggle push never   # no pushes at all; keeps the current mode
/mobile-mode:toggle suggest on   # end each turn with tappable next-step prompts
```

**With function hooks (0.6.0+), you usually don't need the toggle at all:** a
prompt sent from the Remote Control app turns mobile mode on for that turn, and a
prompt typed at the terminal doesn't. The toggle still matters: `off` in a session
opts that session out of the automatic mode, and the push cadence and `suggest`
preferences below apply either way.

On older builds, flip it on when you pick up your phone, off when you sit back down. It applies
to **the session you run it in** and no other. Hooks read the switch at fire
time, so it takes effect on the next turn — no restart. Transitions are
absolute: `on` always means guidance-only, even if `enforce` was set before.

The push cadence sits on top of the mode: `always` (default) pushes one line
every turn, `needed` only when the turn ends with something you'd act on,
`never` not at all. Give it as a second word on `on`/`enforce`/`relax`, or on
its own as `push <cadence>`. Once set it is remembered for the rest of the
session, including across `off`.

Suggestions are a second opt-in preference, off by default. With `suggest on`,
a turn that finishes the work ends with an `AskUserQuestion` offering a few
next-step prompts to tap, instead of just stopping — the same "what next?"
feel some standalone phone clients have. A real decision still comes first when
there is one. It is remembered like the push cadence, and `suggest off` turns
it back off.

## How it works

A function-hook module, two shell hooks, a launcher, and one small state file per session.

**`hooks/register.tsx` (function hooks, Claude Code builds that have them).** Claude
Code stamps every submitted prompt with its origin; a Remote Control prompt arrives
as `origin.kind === "bridge"`. On such a prompt the module attaches the guidance
itself (unless the session's toggle is already on, in which case the shell hook
does), and when the turn ends it calls `PushNotification` with the first line of
the answer — if the push cadence is `always` and the model didn't already push.
Terminal prompts, scheduled tasks and background notifications are never `bridge`,
so unattended sessions are untouched without any switch. An explicit `off` in a
session's state file keeps the module out of that session. Function hooks are an
early-access Claude Code surface; on builds without them the module isn't loaded
and the shell hooks below work exactly as before.

**Shell hooks (every build).**

| | Event | Job |
|---|---|---|
| `run.sh` | — | finds a working Python 3 and runs one handler under it |
| `hooks-handlers/inject.py` | `UserPromptSubmit` | returns `hookSpecificOutput.additionalContext` carrying the mobile guidance |
| `hooks-handlers/enforce.py` | `Stop` | *opt-in*; blocks a turn that ended without `AskUserQuestion`, or (with push cadence `always`) without calling `PushNotification` — at most once per turn |
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

*Updated for 0.6.0:* function hooks **can** detect it — see `register.tsx` above.
What follows still holds for **shell** hooks, which is why the switch remains for
builds without function hooks.

A shell hook receives no indication of where a prompt came from: there's no origin
field in its stdin JSON, and no environment marker for a local session being driven
by Remote Control (`CLAUDE_CODE_REMOTE*` refers to cloud sessions, which is a
different thing).

And a machine-wide switch would be worse than nothing. The moment you flipped it
on from your phone, every unattended session on that box — a chat-channel bridge,
a cron job, a scheduled monitoring loop — would start firing push notifications
at nobody and ending its turns with questions nobody will tap. Keying the switch
on the session id means only the session you toggled changes.

## On enforcement

`enforce` is off by default, and it should probably stay that way.

The Stop hook checks two independent things: did the turn offer anything to
tap, and — only when the push cadence is `always` — did it call
`PushNotification`. It cannot tell *why* either one is missing: "the model got
lazy" and "the work is genuinely finished" look identical from the outside,
and the same goes for a skipped push. Try the guidance alone first; reach for
`enforce` only if the prompt proves too loose in practice. The `needed`/`never`
cadences are never enforced — whether a push was actually needed is a
judgment call the hook can't verify, so it doesn't try.

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
something changed; in `enforce` mode with the default `always` cadence, a
turn where nothing changed but the guidance still asked for a push can cost an
extra round-trip if the model reasonably decided there was nothing worth
pushing. If you run long unattended loops, run them in their own session and
leave mobile mode off there, or set the push cadence to `needed`/`never`.

## Prior art

[Happy](https://github.com/slopus/happy) did this for Claude Code before Remote
Control existed, and the per-message design here is lifted from it: Happy attaches
`meta.appendSystemPrompt` to every message the phone sends (`sync.ts:842`), has the
model emit an `<options>` XML block, then regexes it back out client-side to render
buttons (`parseMarkdownBlock.ts:117`).

The one change is `AskUserQuestion` in place of the XML round-trip. Happy had to
invent a tag and parse it because it controls its own client; a plugin doesn't, and
`AskUserQuestion` already renders as tappable chips with nothing to parse.

One thing worth separating: what Happy calls "suggestions" is *input
autocomplete* — slash-command typeahead and `@file` fuzzy search (ripgrep +
Fuse.js, cached client-side), with no model involved. This plugin's `suggest on`
is a different thing: it asks the main model, in the turn it's already running,
to end with a few context-aware next steps. That's the efficient place for it —
the model already holds the whole conversation, so a subagent or a separate
model call to produce a handful of short prompts would only add cost and latency.

## Development

```bash
python -m pytest -q          # shell hooks
claude plugin test .         # function-hook module (tests/mod.test.ts)
claude plugin validate .claude-plugin/plugin.json
```

The suite runs the handlers the way Claude Code does — through `sh run.sh` and
through the exact command strings in `hooks/hooks.json` — so it needs a POSIX
`sh` on `PATH` (Git Bash on Windows).

## License

MIT

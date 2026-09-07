#!/usr/bin/env python3
"""Backend for /mobile-mode:toggle -- set THIS session's mobile mode.

    toggle.py --session <id> -- [on|enforce|relax [always|needed|never]
                                 | push always|needed|never | off | status]

Mode transitions are absolute, not relative, so the result of a command never
depends on what was set before:

    on       -> "on"       guidance only (clears enforce if it was set)
    enforce  -> "enforce"  guidance + the Stop hook may block an optionless turn
    relax    -> "on"       same as `on`; kept for the README's vocabulary
    off      -> "off"      and, if it was on, flags a one-time retraction for
                           the next turn so the model knows to stop
    status   -> no change

The push cadence is a preference layered on the mode. `on needed` sets both;
`push needed` changes only the cadence (and turns the mode on if it was off).
A cadence once set is remembered for the rest of the session -- across on,
enforce, relax and even off -- so a record with mode "off" may linger just to
carry it. Cadences: always (one push every turn, the default), needed (only
when the turn ends with something to act on), never.

Whenever the result is "enforce", the record also carries skip_next_stop=True:
the toggle's own turn ends with nothing to ask, and the Stop hook consumes that
flag instead of blocking it.

Exit status: 0 success, 64 usage, 65 bad or unexpanded session id, 74 state
could not be written. Failures go to stderr and nothing pretends the toggle
happened.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mobile_mode_state as state  # noqa: E402

ACTIONS = ("on", "off", "enforce", "relax", "status", "push")
USAGE = (
    "usage: /mobile-mode:toggle [on|enforce|relax] [always|needed|never] "
    "| push <always|needed|never> | off | status"
)

EX_USAGE = 64
EX_DATAERR = 65
EX_IOERR = 74

PUSH_WORDS = {"always": "push every turn", "needed": "push when needed", "never": "no push"}


def describe(session_id: str, record: dict) -> str:
    tag = "session %s" % session_id[:8]
    mode = record.get("mode")
    if mode == "off":
        return "mobile mode: OFF [%s]" % tag
    if mode == "enforce":
        head = "ON (enforced - the Stop hook may block a turn that offers nothing to tap)"
    else:
        head = "ON (guidance only)"
    return "mobile mode: %s, %s [%s]" % (head, PUSH_WORDS[state.push_cadence(record)], tag)


def apply(session_id: str, action: str, cadence: str = None) -> dict:
    """Perform one transition and return the resulting record."""
    before = state.load(session_id)
    remembered = {"push": before["push"]} if "push" in before else {}

    if action == "status":
        record = dict(before)
    elif action in ("on", "relax"):
        record = {"mode": "on"}
    elif action == "enforce":
        record = {"mode": "enforce"}
    elif action == "push":
        record = {"mode": "on" if before["mode"] == "off" else before["mode"]}
    else:  # off
        record = {"mode": "off"}
        if before["mode"] != "off" or before.get("retract_pending"):
            record["retract_pending"] = True
        if record == state.OFF and not remembered:
            state.remove(session_id)  # nothing to remember: a clean no-op
            return dict(state.OFF)

    if action != "status":
        record.update(remembered)
        if cadence:
            record["push"] = cadence

    if record["mode"] == "enforce":
        # This very turn is the switch and has nothing to ask. Give the Stop
        # hook one free pass so the toggle is not the first thing it nags about.
        record["skip_next_stop"] = True

    if action == "status" and record == before:
        return record
    state.save(session_id, record)
    return record


def parse_words(words):
    """(action, cadence) from the slash command's words, or None if malformed."""
    if len(words) > 2:
        return None
    action = words[0] if words else "status"
    cadence = words[1] if len(words) > 1 else None
    if action not in ACTIONS:
        return None
    if cadence is not None and cadence not in state.PUSH:
        return None
    if action == "push" and cadence is None:
        return None
    if action in ("off", "status") and cadence is not None:
        return None
    return action, cadence


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mobile-mode toggle", add_help=False, usage=USAGE)
    parser.add_argument("--session", default="")
    parser.add_argument("words", nargs="*")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(USAGE, file=sys.stderr)
        return EX_USAGE

    parsed = parse_words(args.words)
    if parsed is None:
        print(USAGE, file=sys.stderr)
        return EX_USAGE
    action, cadence = parsed

    try:
        session_id = state.validate_session_id(args.session)
    except state.InvalidSessionId as exc:
        print("mobile mode: %s" % exc, file=sys.stderr)
        return EX_DATAERR

    try:
        record = apply(session_id, action, cadence)
    except OSError as exc:
        print("mobile mode: could not write %s: %s" % (state.sessions_dir(), exc), file=sys.stderr)
        return EX_IOERR

    print(describe(session_id, record))
    return 0


if __name__ == "__main__":
    sys.exit(main())

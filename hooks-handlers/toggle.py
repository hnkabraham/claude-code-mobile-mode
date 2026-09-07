#!/usr/bin/env python3
"""Backend for /mobile-mode:toggle -- set THIS session's mobile mode.

    toggle.py --session <id> -- [on | off | enforce | relax | status]

Transitions are absolute, not relative, so the result of a command never
depends on what was set before:

    on       -> "on"       guidance only (clears enforce if it was set)
    enforce  -> "enforce"  guidance + the Stop hook may block an optionless turn
    relax    -> "on"       same as `on`; kept for the README's vocabulary
    off      -> "off"      and, if it was on, flags a one-time retraction for
                           the next turn so the model knows to stop
    status   -> no change

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

ACTIONS = ("on", "off", "enforce", "relax", "status")

EX_USAGE = 64
EX_DATAERR = 65
EX_IOERR = 74


def describe(session_id: str, record: dict) -> str:
    tag = "session %s" % session_id[:8]
    mode = record.get("mode")
    if mode == "enforce":
        return "mobile mode: ON (enforced - the Stop hook may block a turn that offers nothing to tap) [%s]" % tag
    if mode == "on":
        return "mobile mode: ON (guidance only) [%s]" % tag
    return "mobile mode: OFF [%s]" % tag


def apply(session_id: str, action: str) -> dict:
    """Perform one transition and return the resulting record."""
    before = state.load(session_id)
    if action == "status":
        record = dict(before)
    elif action in ("on", "relax"):
        record = {"mode": "on"}
    elif action == "enforce":
        record = {"mode": "enforce"}
    else:  # off
        if before["mode"] == "off":
            state.remove(session_id)
            return dict(state.OFF)
        record = {"mode": "off", "retract_pending": True}

    if record["mode"] == "enforce":
        # This very turn is the switch and has nothing to ask. Give the Stop
        # hook one free pass so the toggle is not the first thing it nags about.
        record["skip_next_stop"] = True

    if action == "status" and record == before:
        return record
    state.save(session_id, record)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="mobile-mode toggle",
        add_help=False,
        usage="toggle.py --session <id> -- [on|off|enforce|relax|status]",
    )
    parser.add_argument("--session", default="")
    parser.add_argument("action", nargs="?", default="status")
    parser.add_argument("extra", nargs="*")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        print(parser.usage, file=sys.stderr)
        return EX_USAGE

    if args.extra or args.action not in ACTIONS:
        print("usage: /mobile-mode:toggle [on|off|enforce|relax|status]", file=sys.stderr)
        return EX_USAGE

    try:
        session_id = state.validate_session_id(args.session)
    except state.InvalidSessionId as exc:
        print("mobile mode: %s" % exc, file=sys.stderr)
        return EX_DATAERR

    try:
        record = apply(session_id, args.action)
    except OSError as exc:
        print("mobile mode: could not write %s: %s" % (state.sessions_dir(), exc), file=sys.stderr)
        return EX_IOERR

    print(describe(session_id, record))
    return 0


if __name__ == "__main__":
    sys.exit(main())

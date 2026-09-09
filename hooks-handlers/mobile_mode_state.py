"""Per-session mobile-mode state.

One small JSON file per Claude Code session under
~/.claude/mobile-mode/sessions/<session_id>.json. The session id is the one
thing both sides can see: the slash command gets it through the
`${CLAUDE_SESSION_ID}` substitution, and the hooks get it as `session_id` on
stdin. Nothing here is global on purpose -- flipping mobile mode on from your
phone must not touch the unattended sessions (cron jobs, chat bridges,
scheduled loops) that share the same machine and home directory.

Modes:
  "on"       inject the guidance every turn (guidance only)
  "enforce"  also let the Stop hook block a turn that offered nothing to tap,
             and -- when push cadence is "always" -- a turn that never called
             PushNotification
  "off"      nothing injected; a record may linger with retract_pending=True
             so the next turn can tell the model the earlier guidance no
             longer applies

Push cadence (record["push"], default "always"): how often the guidance asks
for a PushNotification -- "always" every turn, "needed" only when the turn
ends with something to act on, "never" not at all. It is a preference, not a
mode, so it survives on/enforce/relax/off within the session; a record with
mode "off" may linger just to remember it.

Suggestions (record["suggest"], default off): when True, the guidance asks the
model to end even a finished turn with an AskUserQuestion offering next-step
prompts to tap. Like the push cadence it is a remembered preference, carried
across mode changes and off.

Writes are atomic (temp file + os.replace). Records older than
PRUNE_AFTER_DAYS are removed opportunistically on every write, so abandoned
sessions do not accumulate.
"""

import json
import os
import re
import tempfile
import time

MODES = ("on", "enforce", "off")
PUSH = ("always", "needed", "never")
DEFAULT_PUSH = "always"
OFF = {"mode": "off"}
PRUNE_AFTER_DAYS = 14

# Claude Code session ids are UUIDs. Accept anything path-safe of plausible
# length so a future id format does not brick the plugin, but never an empty
# string, a path separator, or an unexpanded "${CLAUDE_SESSION_ID}".
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


class InvalidSessionId(ValueError):
    """The session id is missing, unexpanded, or not path-safe."""


def sessions_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "mobile-mode", "sessions")


def validate_session_id(session_id) -> str:
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise InvalidSessionId(
            "no usable session id (got %r). The toggle needs Claude Code's "
            "${CLAUDE_SESSION_ID} substitution, available in 2.1.9 and later."
            % (session_id,)
        )
    return session_id


def record_path(session_id) -> str:
    return os.path.join(sessions_dir(), validate_session_id(session_id) + ".json")


def load(session_id) -> dict:
    """The session's record, or {"mode": "off"} when there is none.

    Never raises: an invalid id, a missing file, or a corrupt file all read as
    "off", because the hooks must stay silent rather than break a turn.
    """
    try:
        path = record_path(session_id)
    except InvalidSessionId:
        return dict(OFF)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return dict(OFF)
    if not isinstance(data, dict) or data.get("mode") not in MODES:
        return dict(OFF)
    if "push" in data and data["push"] not in PUSH:
        data.pop("push")  # unknown cadence reads as the default, never as a fault
    if "suggest" in data and not isinstance(data["suggest"], bool):
        data.pop("suggest")  # only a real bool counts; anything else means off
    return data


def push_cadence(record: dict) -> str:
    """The record's push cadence, defaulting when absent or unrecognised."""
    cadence = record.get("push") if isinstance(record, dict) else None
    return cadence if cadence in PUSH else DEFAULT_PUSH


def suggest_on(record: dict) -> bool:
    """Whether the record opts in to end-of-turn next-step suggestions."""
    return bool(record.get("suggest")) if isinstance(record, dict) else False


def mode(session_id) -> str:
    return load(session_id)["mode"]


def save(session_id, record: dict) -> None:
    """Atomically replace the session's record. Raises on failure."""
    if record.get("mode") not in MODES:
        raise ValueError("record.mode must be one of %s" % (MODES,))
    if "push" in record and record["push"] not in PUSH:
        raise ValueError("record.push must be one of %s" % (PUSH,))
    if "suggest" in record and not isinstance(record["suggest"], bool):
        raise ValueError("record.suggest must be a bool")
    path = record_path(session_id)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    prune()


def remove(session_id) -> None:
    """Delete the session's record if it exists. Raises on other failures."""
    try:
        os.unlink(record_path(session_id))
    except FileNotFoundError:
        pass


def prune(now: float = None, max_age_days: float = PRUNE_AFTER_DAYS) -> int:
    """Best-effort removal of records older than max_age_days. Never raises."""
    removed = 0
    cutoff = (time.time() if now is None else now) - max_age_days * 86400
    try:
        names = os.listdir(sessions_dir())
    except OSError:
        return 0
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(sessions_dir(), name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed

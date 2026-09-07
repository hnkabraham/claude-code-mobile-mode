"""Tests for the mobile-mode plugin.

Run from the repo root:  python -m pytest -q

Unit tests import the handlers directly. Integration tests run them the way
Claude Code does -- through `sh run.sh` and through the exact command strings
in hooks/hooks.json -- and need a POSIX sh on PATH (Git Bash on Windows).
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HANDLERS = REPO / "hooks-handlers"
sys.path.insert(0, str(HANDLERS))

import mobile_mode_state as state  # noqa: E402
import enforce  # noqa: E402
import inject  # noqa: E402
import toggle  # noqa: E402

SH = shutil.which("sh")
BASH = shutil.which("bash")
needs_sh = pytest.mark.skipif(SH is None, reason="needs a POSIX sh (Git Bash on Windows)")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash")
WINDOWS = sys.platform.startswith("win")

SID = "b9fabb6a-e810-480a-a51c-7e44107feb22"
OTHER = "0f0f0f0f-1111-2222-3333-444444444444"


# --------------------------------------------------------------------------- helpers

@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolated home for both in-process code (expanduser) and subprocesses (env)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    return h


def run(args, stdin="", env=None, cwd=None):
    return subprocess.run(
        args, input=stdin, capture_output=True, text=True,
        env=env if env is not None else dict(os.environ), cwd=cwd,
    )


def run_handler(name, stdin="", env=None, argv=()):
    return run([sys.executable, str(HANDLERS / ("%s.py" % name)), *argv], stdin=stdin, env=env)


def hook_event(session_id=SID, **extra):
    event = {"session_id": session_id, "cwd": str(REPO), "hook_event_name": "UserPromptSubmit"}
    event.update(extra)
    return json.dumps(event)


def set_mode(session_id, action):
    assert toggle.main(["--session", session_id, "--", action]) == 0


def arm_enforce(session_id):
    """Enforcement with the toggle turn's free pass already consumed."""
    state.save(session_id, {"mode": "enforce"})


def user_text(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def user_blocks(*blocks):
    return {"type": "user", "message": {"role": "user", "content": list(blocks)}}


def assistant(*blocks):
    return {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}}


def text(t):
    return {"type": "text", "text": t}


def tool_use(name, tid="toolu_1"):
    return {"type": "tool_use", "id": tid, "name": name, "input": {}}


def tool_result(tid="toolu_1", is_error=False):
    block = {"type": "tool_result", "tool_use_id": tid, "content": "ok"}
    if is_error:
        block["is_error"] = True
    return block


def write_transcript(path, entries):
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry if isinstance(entry, str) else json.dumps(entry))
            fh.write("\n")
    return str(path)


def make_exe(path, body):
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# --------------------------------------------------------------------------- state

def test_missing_record_reads_off(home):
    assert state.load(SID) == {"mode": "off"}
    assert state.mode(SID) == "off"


def test_save_and_load_roundtrip(home):
    state.save(SID, {"mode": "enforce"})
    assert state.load(SID) == {"mode": "enforce"}
    assert (home / ".claude" / "mobile-mode" / "sessions" / (SID + ".json")).is_file()


@pytest.mark.parametrize("bad", ["", "${CLAUDE_SESSION_ID}", "../escape", "a/b", "short", None, 42, "$" + SID])
def test_invalid_session_ids_are_rejected(home, bad):
    with pytest.raises(state.InvalidSessionId):
        state.validate_session_id(bad)
    assert state.load(bad) == {"mode": "off"}  # and reading one never raises


def test_corrupt_or_unknown_record_reads_off(home):
    path = Path(state.record_path(SID))
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert state.load(SID) == {"mode": "off"}
    path.write_text(json.dumps({"mode": "loud"}), encoding="utf-8")
    assert state.load(SID) == {"mode": "off"}
    path.write_text(json.dumps(["on"]), encoding="utf-8")
    assert state.load(SID) == {"mode": "off"}


def test_save_leaves_no_temp_files(home):
    state.save(SID, {"mode": "on"})
    names = os.listdir(state.sessions_dir())
    assert names == [SID + ".json"]


def test_save_rejects_unknown_mode(home):
    with pytest.raises(ValueError):
        state.save(SID, {"mode": "sideways"})


def test_prune_removes_only_old_records(home):
    state.save(SID, {"mode": "on"})
    state.save(OTHER, {"mode": "on"})
    old = state.record_path(OTHER)
    stale = time.time() - 20 * 86400
    os.utime(old, (stale, stale))
    assert state.prune() == 1
    assert state.load(SID) == {"mode": "on"}
    assert state.load(OTHER) == {"mode": "off"}


def test_sessions_are_independent(home):
    state.save(SID, {"mode": "enforce"})
    assert state.load(OTHER) == {"mode": "off"}


# --------------------------------------------------------------------------- toggle

def test_toggle_on(home, capsys):
    assert toggle.main(["--session", SID, "--", "on"]) == 0
    assert state.mode(SID) == "on"
    out = capsys.readouterr().out
    assert "ON (guidance only)" in out and SID[:8] in out


def test_toggle_enforce(home, capsys):
    set_mode(SID, "enforce")
    assert state.load(SID) == {"mode": "enforce", "skip_next_stop": True}
    assert "enforced" in capsys.readouterr().out


def test_only_enforce_results_grant_a_stop_pass(home):
    set_mode(SID, "enforce")
    state.save(SID, {"mode": "enforce"})  # pass consumed by a Stop
    set_mode(SID, "status")
    assert state.load(SID) == {"mode": "enforce", "skip_next_stop": True}, "status re-arms the pass"
    set_mode(SID, "on")
    assert state.load(SID) == {"mode": "on"}
    set_mode(SID, "status")
    assert state.load(SID) == {"mode": "on"}
    set_mode(SID, "off")
    assert state.load(SID) == {"mode": "off", "retract_pending": True}


def test_on_after_enforce_is_guidance_only(home):
    set_mode(SID, "enforce")
    set_mode(SID, "on")
    assert state.mode(SID) == "on"


def test_relax_is_on(home):
    set_mode(SID, "enforce")
    set_mode(SID, "relax")
    assert state.mode(SID) == "on"
    set_mode(OTHER, "relax")
    assert state.mode(OTHER) == "on"


def test_off_from_on_queues_retraction(home, capsys):
    set_mode(SID, "on")
    set_mode(SID, "off")
    assert state.load(SID) == {"mode": "off", "retract_pending": True}
    assert "OFF" in capsys.readouterr().out


def test_off_from_off_is_a_clean_noop(home):
    set_mode(SID, "off")
    assert not os.path.exists(state.record_path(SID))
    assert state.load(SID) == {"mode": "off"}


def test_status_changes_nothing(home, capsys):
    set_mode(SID, "status")
    assert not os.path.exists(state.sessions_dir())
    assert "OFF" in capsys.readouterr().out
    set_mode(SID, "enforce")
    set_mode(SID, "status")
    assert state.mode(SID) == "enforce"


def test_empty_arguments_mean_status(home):
    assert toggle.main(["--session", SID, "--"]) == 0
    assert not os.path.exists(state.sessions_dir())


@pytest.mark.parametrize("session", ["", "${CLAUDE_SESSION_ID}"])
def test_missing_or_unexpanded_session_id_fails_loudly(home, capsys, session):
    assert toggle.main(["--session", session, "--", "on"]) == toggle.EX_DATAERR
    err = capsys.readouterr().err
    assert "session id" in err
    assert not os.path.exists(state.sessions_dir())


@pytest.mark.parametrize("argv", [["bogus"], ["on", "please"], ["off", "now"]])
def test_bad_arguments_are_usage_errors(home, argv):
    assert toggle.main(["--session", SID, "--", *argv]) == toggle.EX_USAGE
    assert not os.path.exists(state.sessions_dir())


def test_unwritable_state_dir_is_reported(home, capsys):
    blocker = home / ".claude" / "mobile-mode"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not a directory", encoding="utf-8")
    assert toggle.main(["--session", SID, "--", "on"]) == toggle.EX_IOERR
    assert "could not write" in capsys.readouterr().err


def test_toggle_does_not_touch_other_sessions(home):
    set_mode(SID, "enforce")
    assert state.mode(OTHER) == "off"
    set_mode(OTHER, "off")
    assert state.mode(SID) == "enforce"


# --------------------------------------------------------------------------- inject

def test_inject_off_is_silent(home):
    assert inject.decide({"session_id": SID, "prompt": "hi"}) == ""


@pytest.mark.parametrize("action", ["on", "enforce"])
def test_inject_on_returns_guidance(home, action):
    set_mode(SID, action)
    assert inject.decide({"session_id": SID, "prompt": "hi"}) == inject.GUIDANCE


def test_inject_ignores_other_sessions(home):
    set_mode(OTHER, "enforce")
    assert inject.decide({"session_id": SID, "prompt": "hi"}) == ""


def test_inject_skips_the_toggle_command_itself(home):
    set_mode(SID, "on")
    assert inject.decide({"session_id": SID, "prompt": "/mobile-mode:toggle off"}) == ""
    assert inject.decide({"session_id": SID, "prompt": "  /mobile-mode:toggle status"}) == ""
    assert inject.decide({"session_id": SID, "prompt": "tell me about /mobile-mode:toggle"}) == inject.GUIDANCE


def test_inject_retracts_exactly_once_after_off(home):
    set_mode(SID, "on")
    set_mode(SID, "off")
    assert inject.decide({"session_id": SID, "prompt": "next"}) == inject.RETRACTION
    assert not os.path.exists(state.record_path(SID))
    assert inject.decide({"session_id": SID, "prompt": "and again"}) == ""


def test_inject_no_retraction_when_it_was_never_on(home):
    set_mode(SID, "off")
    assert inject.decide({"session_id": SID, "prompt": "next"}) == ""


def test_inject_handles_missing_fields(home):
    assert inject.decide({}) == ""
    assert inject.decide({"session_id": None, "prompt": None}) == ""


def test_inject_main_emits_hook_shape(home):
    set_mode(SID, "on")
    r = run_handler("inject", stdin=hook_event(prompt="hi"))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "<mobile-mode>" in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("stdin", ["", "   ", "not json", "null", "[1, 2]", '"str"'])
def test_inject_main_is_silent_on_bad_stdin(home, stdin):
    r = run_handler("inject", stdin=stdin)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "{}"


def test_inject_main_drains_large_stdin_on_off_path(home):
    payload = json.dumps({"session_id": SID, "prompt": "x" * (1 << 20)})
    r = run_handler("inject", stdin=payload)
    assert r.returncode == 0 and r.stdout.strip() == "{}"


# --------------------------------------------------------------------------- enforce: transcript parsing

def test_plain_prompt_then_text_offers_nothing(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [user_text("do it"), assistant(text("done"))])
    assert enforce.offered_options(t) is False


def test_text_block_prompt_is_also_a_boundary(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [user_blocks(text("do it")), assistant(text("done"))])
    assert enforce.offered_options(t) is False


def test_question_in_this_turn_counts(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("do it"),
        assistant(text("which?"), tool_use("AskUserQuestion")),
    ])
    assert enforce.offered_options(t) is True


def test_answered_question_then_more_work_still_counts(tmp_path):
    # Lenient by design: the turn did ask; what it did after the tap is its business.
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("do it"),
        assistant(tool_use("AskUserQuestion")),
        user_blocks(tool_result()),
        assistant(text("done that")),
    ])
    assert enforce.offered_options(t) is True


def test_cancelled_or_failed_question_does_not_count(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("do it"),
        assistant(tool_use("AskUserQuestion")),
        user_blocks(tool_result(is_error=True)),
        assistant(text("ok never mind")),
    ])
    assert enforce.offered_options(t) is False


def test_ordinary_tool_results_are_skipped_not_boundaries(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("do it"),
        assistant(tool_use("AskUserQuestion", "toolu_q")),
        user_blocks(tool_result("toolu_q")),
        assistant(tool_use("Bash", "toolu_b")),
        user_blocks(tool_result("toolu_b")),
        assistant(text("done")),
    ])
    assert enforce.offered_options(t) is True
    t2 = write_transcript(tmp_path / "t2.jsonl", [
        user_text("do it"),
        assistant(tool_use("Bash", "toolu_b")),
        user_blocks(tool_result("toolu_b", is_error=True)),
        assistant(text("done")),
    ])
    assert enforce.offered_options(t2) is False


def test_previous_turns_question_does_not_count(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("first"),
        assistant(tool_use("AskUserQuestion")),
        user_blocks(tool_result()),
        assistant(text("ok")),
        user_text("second"),
        assistant(text("done")),
    ])
    assert enforce.offered_options(t) is False


@pytest.mark.parametrize("entries", [
    [],
    [{"type": "summary", "summary": "..."}],
    [{"type": "system", "content": "..."}, {"type": "progress"}],
    [assistant(text("orphan"))],
])
def test_no_user_boundary_is_unknown(tmp_path, entries):
    t = write_transcript(tmp_path / "t.jsonl", entries)
    assert enforce.offered_options(t) is None


def test_missing_transcript_is_unknown(tmp_path):
    assert enforce.offered_options(str(tmp_path / "nope.jsonl")) is None


def test_garbage_lines_are_skipped_not_fatal(tmp_path):
    t = write_transcript(tmp_path / "t.jsonl", [
        user_text("do it"),
        assistant(text("done")),
        "null", "[1,2]", "{not json", "", '"just a string"', json.dumps({"message": "not a dict"}),
    ])
    assert enforce.offered_options(t) is False


def test_real_transcript_if_provided():
    path = os.environ.get("MOBILE_MODE_REAL_TRANSCRIPT")
    if not path or not os.path.exists(path):
        pytest.skip("set MOBILE_MODE_REAL_TRANSCRIPT=<jsonl> to run against a real transcript")
    started = time.perf_counter()
    result = enforce.offered_options(path)
    assert result in (True, False, None)
    assert time.perf_counter() - started < 2.0


# --------------------------------------------------------------------------- enforce: hook behaviour

@pytest.fixture
def transcripts(tmp_path):
    return {
        "asked": write_transcript(tmp_path / "asked.jsonl", [user_text("q"), assistant(tool_use("AskUserQuestion"))]),
        "silent": write_transcript(tmp_path / "silent.jsonl", [user_text("q"), assistant(text("done"))]),
        "empty": write_transcript(tmp_path / "empty.jsonl", []),
        "missing": str(tmp_path / "missing.jsonl"),
    }


def stop_event(transcript, session_id=SID, **extra):
    return hook_event(session_id, hook_event_name="Stop", transcript_path=transcript, **extra)


def test_enforce_gives_the_toggle_turn_a_free_pass(home, transcripts):
    set_mode(SID, "enforce")  # what /mobile-mode:toggle enforce does
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"]))
    assert r.stdout.strip() == "{}"
    assert state.load(SID) == {"mode": "enforce"}, "the pass is consumed"
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"]))
    assert json.loads(r.stdout)["decision"] == "block", "the next turn is fair game"


def test_enforce_blocks_a_silent_turn(home, transcripts):
    arm_enforce(SID)
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"]))
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["decision"] == "block" and "AskUserQuestion" in out["reason"]


def test_enforce_lets_a_turn_with_a_question_end(home, transcripts):
    arm_enforce(SID)
    r = run_handler("enforce", stdin=stop_event(transcripts["asked"]))
    assert r.stdout.strip() == "{}"


@pytest.mark.parametrize("action", ["on", "off"])
def test_enforce_is_inert_unless_mode_is_enforce(home, transcripts, action):
    set_mode(SID, action)
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"]))
    assert r.stdout.strip() == "{}"


def test_enforce_never_blocks_twice(home, transcripts):
    arm_enforce(SID)
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"], stop_hook_active=True))
    assert r.stdout.strip() == "{}"


@pytest.mark.parametrize("which", ["empty", "missing"])
def test_enforce_allows_when_transcript_is_unknowable(home, transcripts, which):
    arm_enforce(SID)
    r = run_handler("enforce", stdin=stop_event(transcripts[which]))
    assert r.stdout.strip() == "{}"


def test_enforce_allows_without_transcript_path(home):
    arm_enforce(SID)
    r = run_handler("enforce", stdin=hook_event(hook_event_name="Stop"))
    assert r.stdout.strip() == "{}"


def test_enforce_ignores_other_sessions(home, transcripts):
    arm_enforce(OTHER)
    r = run_handler("enforce", stdin=stop_event(transcripts["silent"], session_id=SID))
    assert r.stdout.strip() == "{}"


@pytest.mark.parametrize("stdin", ["", "not json", "null", "[]"])
def test_enforce_is_silent_on_bad_stdin(home, stdin):
    r = run_handler("enforce", stdin=stdin)
    assert r.returncode == 0 and r.stdout.strip() == "{}"


# --------------------------------------------------------------------------- launcher

@pytest.fixture
def fake_bin(tmp_path):
    """A PATH prefix with controllable python3 / py / python entries.

    Each entry logs its invocation to <name>.log so tests can prove which
    interpreter actually ran the handler and how many times.
    """
    d = tmp_path / "bin"
    d.mkdir()
    real = sys.executable.replace("\\", "/")

    def stub(name):  # the Microsoft Store alias behaviour
        make_exe(d / name, 'echo "$@" >> "%s/%s.log"\necho "Python was not found" >&2\nexit 9009\n' % (d.as_posix(), name))

    def wrapper(name, drop_first=False):  # a real interpreter that logs
        drop = "shift\n" if drop_first else ""
        make_exe(d / name, '%secho "$@" >> "%s/%s.log"\nexec "%s" "$@"\n' % (drop, d.as_posix(), name, real))

    def path_env(*extra_dirs):
        env = dict(os.environ)
        parts = [str(d), *extra_dirs]
        env["PATH"] = os.pathsep.join(parts)
        return env

    def log(name):
        p = d / (name + ".log")
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    return type("FakeBin", (), {"dir": d, "stub": staticmethod(stub), "wrapper": staticmethod(wrapper),
                                "path_env": staticmethod(path_env), "log": staticmethod(log)})


def system_dirs():
    """Where sh, dirname and friends live, so a restricted PATH still has them."""
    dirs = []
    for tool in ("sh", "dirname"):
        found = shutil.which(tool)
        if found:
            dirs.append(os.path.dirname(found))
    return list(dict.fromkeys(dirs))


@needs_sh
def test_launcher_usage_errors():
    assert run([SH, str(REPO / "run.sh")]).returncode == 64
    assert run([SH, str(REPO / "run.sh"), "nonexistent"]).returncode == 64


@needs_sh
def test_launcher_skips_the_store_stub_and_runs_once(home, fake_bin):
    set_mode(SID, "on")
    fake_bin.stub("python3")
    fake_bin.wrapper("py", drop_first=True)   # `py -3 script` -> drop the -3
    fake_bin.wrapper("python")
    env = fake_bin.path_env(*system_dirs())
    r = run([SH, str(REPO / "run.sh"), "inject"], stdin=hook_event(prompt="hi"), env=env)
    assert r.returncode == 0, r.stderr
    assert "<mobile-mode>" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert fake_bin.log("python3"), "the stub should have been tried first"
    handler_runs = [l for name in ("py", "python") for l in fake_bin.log(name) if "inject.py" in l]
    assert len(handler_runs) == 1, handler_runs


@needs_sh
def test_launcher_prefers_a_real_python3(home, fake_bin):
    set_mode(SID, "on")
    fake_bin.wrapper("python3")
    fake_bin.stub("py")
    fake_bin.stub("python")
    env = fake_bin.path_env(*system_dirs())
    r = run([SH, str(REPO / "run.sh"), "inject"], stdin=hook_event(prompt="hi"), env=env)
    assert r.returncode == 0, r.stderr
    assert "<mobile-mode>" in r.stdout
    assert len([l for l in fake_bin.log("python3") if "inject.py" in l]) == 1
    assert not fake_bin.log("py") and not fake_bin.log("python")


@needs_sh
def test_launcher_reports_no_python(home, fake_bin):
    for name in ("python3", "py", "python"):
        fake_bin.stub(name)
    env = fake_bin.path_env(*system_dirs())
    r = run([SH, str(REPO / "run.sh"), "inject"], stdin=hook_event(), env=env)
    assert r.returncode == 69
    assert "no Python 3.8+" in r.stderr


@needs_sh
def test_launcher_preserves_handler_exit_status(home):
    r = run([SH, str(REPO / "run.sh"), "toggle", "--session", SID, "--", "bogus"])
    assert r.returncode == toggle.EX_USAGE
    r = run([SH, str(REPO / "run.sh"), "toggle", "--session", "", "--", "on"])
    assert r.returncode == toggle.EX_DATAERR
    r = run([SH, str(REPO / "run.sh"), "toggle", "--session", SID, "--", "on"])
    assert r.returncode == 0 and "ON (guidance only)" in r.stdout


@needs_sh
@pytest.mark.skipif(not WINDOWS, reason="backslash paths are a Windows concern")
def test_launcher_accepts_a_native_windows_path(home):
    set_mode(SID, "on")
    native = str(REPO / "run.sh")
    assert "\\" in native
    r = run([SH, native, "inject"], stdin=hook_event(prompt="hi"))
    assert r.returncode == 0, r.stderr
    assert "<mobile-mode>" in r.stdout


# --------------------------------------------------------------------------- hooks.json, end to end

def hook_commands():
    data = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    return {event: groups[0]["hooks"][0]["command"] for event, groups in data["hooks"].items()}


@pytest.fixture
def awkward_install(tmp_path):
    """A copy of the plugin at a path with a space and, on Windows, a \\t component."""
    root = tmp_path / "mobile mode" / "tools"
    shutil.copytree(REPO, root, ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", ".pytest_cache"))
    return root


@needs_bash
@pytest.mark.parametrize("install", ["repo", "awkward"])
def test_hooks_json_commands_run_as_claude_code_runs_them(home, tmp_path, awkward_install, install):
    root = REPO if install == "repo" else awkward_install
    plugin_root = str(root)  # native form: backslashes on Windows, like ${CLAUDE_PLUGIN_ROOT}
    arm_enforce(SID)
    silent = write_transcript(tmp_path / "silent.jsonl", [user_text("q"), assistant(text("done"))])
    commands = hook_commands()
    assert set(commands) == {"UserPromptSubmit", "Stop"}

    for event, command in commands.items():
        assert "${CLAUDE_PLUGIN_ROOT}" in command
        assert '"${CLAUDE_PLUGIN_ROOT}' in command, "plugin path must be quoted"
        resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", plugin_root)
        stdin = hook_event(prompt="hi") if event == "UserPromptSubmit" else stop_event(silent)
        r = run([BASH, "-c", resolved], stdin=stdin)
        assert r.returncode == 0, (event, resolved, r.stderr)
        out = json.loads(r.stdout)
        if event == "UserPromptSubmit":
            assert "<mobile-mode>" in out["hookSpecificOutput"]["additionalContext"]
        else:
            assert out["decision"] == "block"


@needs_bash
def test_hooks_json_commands_are_silent_when_off(home, tmp_path):
    silent = write_transcript(tmp_path / "silent.jsonl", [user_text("q"), assistant(text("done"))])
    for event, command in hook_commands().items():
        resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO))
        stdin = hook_event(prompt="hi") if event == "UserPromptSubmit" else stop_event(silent)
        r = run([BASH, "-c", resolved], stdin=stdin)
        assert r.returncode == 0 and r.stdout.strip() == "{}", (event, r.stderr)


# --------------------------------------------------------------------------- packaging sanity

def test_command_file_uses_plugin_root_and_session_id():
    body = (REPO / "commands" / "toggle.md").read_text(encoding="utf-8")
    assert '"${CLAUDE_PLUGIN_ROOT}/run.sh"' in body
    assert '"${CLAUDE_SESSION_ID}"' in body
    assert "disable-model-invocation: true" in body
    assert "~/.claude/skills" not in body


def test_no_stale_references_to_the_old_layout():
    offenders = []
    for path in REPO.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "tests" in path.parts:
            continue
        if path.suffix in (".png", ".pyc"):
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("toggle.sh", "~/.claude/.mobile-mode", "skills/mobile-mode/toggle"):
            if needle in body:
                offenders.append("%s: %s" % (path.relative_to(REPO), needle))
    assert not offenders, offenders


def test_manifests_agree():
    plugin = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "mobile-mode"
    assert "skills" not in plugin and "commands" not in plugin, "rely on default discovery"
    assert market["plugins"][0]["version"] == plugin["version"]
    assert (REPO / "skills" / "mobile-mode" / "SKILL.md").is_file()
    assert not (REPO / "SKILL.md").exists()


def test_launcher_has_unix_line_endings():
    # A CRLF run.sh breaks sh on every platform; .gitattributes pins it to LF.
    assert b"\r" not in (REPO / "run.sh").read_bytes()
    assert "*.sh text eol=lf" in (REPO / ".gitattributes").read_text(encoding="utf-8")

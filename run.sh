#!/bin/sh
# Launcher: run one of the plugin's Python handlers under the first working
# Python 3 on PATH.
#
#   sh run.sh inject   < hook-stdin
#   sh run.sh enforce  < hook-stdin
#   sh run.sh toggle --session <id> -- on
#
# Why not just `python3`: on stock Windows, `python3` is the Microsoft Store
# App Execution Alias stub. It exits 9009 ("Python was not found") without
# running anything, while the real interpreter is `py` or `python`. Each
# candidate is probed with a tiny -c snippet that never touches stdin, and the
# first real Python 3.8+ runs the handler exactly once via exec, so the
# handler's stdin and exit status pass through untouched.
#
# Needs a POSIX sh: /bin/sh on macOS/Linux, Git Bash on Windows (which is also
# the shell Claude Code itself uses for hooks there).

set -u

here=$(dirname "$0")
name=${1:-}
if [ -z "$name" ]; then
    echo "run.sh: missing handler name (inject|enforce|toggle)" >&2
    exit 64
fi
shift
script="$here/hooks-handlers/$name.py"
if [ ! -f "$script" ]; then
    echo "run.sh: no such handler: $script" >&2
    exit 64
fi

probe='import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)'
for candidate in "python3" "py -3" "python"; do
    # shellcheck disable=SC2086  # word splitting is intended for "py -3"
    if $candidate -c "$probe" </dev/null >/dev/null 2>&1; then
        exec $candidate "$script" "$@"
    fi
done

echo "run.sh: no Python 3.8+ found on PATH (tried python3, py -3, python)" >&2
exit 69

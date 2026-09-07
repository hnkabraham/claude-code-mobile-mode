---
description: Turn mobile mode on or off for this session (tappable options + a push after each turn)
argument-hint: on | off | enforce | relax | status
disable-model-invocation: true
allowed-tools: Bash(sh:*)
---

Mobile mode switch for this session. Result of running it:

!`sh "${CLAUDE_PLUGIN_ROOT}/run.sh" toggle --session "${CLAUDE_SESSION_ID}" -- $ARGUMENTS`

Report the result line above to the user verbatim and stop. Do not explain the
modes unless asked. Do not call AskUserQuestion or PushNotification for this
reply -- it is the switch, not a turn worth a tap.

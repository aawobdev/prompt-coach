---
description: Run a prompt-coach command (report, dash, stats, discover) and show the result
argument-hint: [report|dash|stats|discover] [options...]
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pc *)
---

!`${CLAUDE_PLUGIN_ROOT}/scripts/pc $ARGUMENTS`

Relay the output above as-is -- don't re-summarize or shorten it. If anything in it looks surprising or worth flagging, add one short note after.

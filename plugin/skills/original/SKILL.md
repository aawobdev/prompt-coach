---
description: Send your original prompt as-is, ignoring the prompt-coach rewrite suggestion
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pc nudge-consume:*)
---

!`${CLAUDE_PLUGIN_ROOT}/scripts/pc nudge-consume --want original --session "${CLAUDE_SESSION_ID}"`

If the line above starts with "(no prompt-coach rewrite" just tell me that plainly. Otherwise treat the text above as my actual next message and respond to it directly -- it is not a report to relay or summarize.

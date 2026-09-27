---
description: Send the prompt-coach rewrite that was just held back
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pc nudge-consume:*)
---

!`${CLAUDE_PLUGIN_ROOT}/scripts/pc nudge-consume --want rewritten --session "${CLAUDE_SESSION_ID}"`

If the line above starts with "(no prompt-coach rewrite" just tell me that plainly. Otherwise treat the text above as my actual next message and respond to it directly -- it is not a report to relay or summarize. I can't see that text in the chat once this command runs, so begin your reply with a blockquote showing it verbatim, headed `**Sent (prompt-coach rewrite):**`, then answer it below.

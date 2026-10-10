# Security policy

prompt-coach reads personal prompt history, so privacy bugs count as
security bugs. Examples: prompt content reaching a network endpoint the user
did not configure, the remote-endpoint guard being bypassed, a source store
being written to, or an API key appearing in logs, errors or the cache.

## Reporting

Please report privately through GitHub's
[private vulnerability reporting](https://github.com/aawobdev/prompt-coach/security/advisories/new)
rather than a public issue. Expect an acknowledgement within a week.

## Supported versions

Only the latest commit on `main` is supported.

# Security Remediation Plan - prompt-coach

_Generated 2026-10-08 from the trivy full scan (weekly CVE job), re-run 2026-10-08._

This document lists the dependency vulnerabilities found in this repo and what needs to change. It is a plan, not a fix - apply it deliberately and run the test suite afterwards.

**Summary:** 2 findings across 2 packages - 🔴 0 critical, 🟠 1 high, 🟡 1 medium.

## Vulnerable packages

| Package | Installed | 🔴 | 🟠 | 🟡 | Fixed version |
|---|---|---|---|---|---|
| `black` | 24.10.0 | 0 | 1 | 0 | 26.3.1 |
| `pytest` | 8.4.2 | 0 | 0 | 1 | 9.0.3 |

## How to fix

This repo uses `uv` (house convention: `uv.lock` + `pyproject.toml`).

```bash
uv lock --upgrade-package black    # to >= 26.3.1
uv lock --upgrade-package pytest    # to >= 9.0.3
uv sync
# run the test suite before committing
```

If a package is a transitive dependency, `uv lock --upgrade-package` still bumps it. Constrain in `pyproject.toml` if you need a floor.

## Notes

- Severity counts are per (CVE, package) pair; the same upstream bug often appears under several transitive dependency paths.
- Some advisories are fixed in multiple release lines (e.g. `7.5.5` or `8.0.1`) - pick the line that matches your major version.
- Fixes can introduce breaking changes at major-version boundaries. Run the test suite and, for deployed apps, verify the running service afterwards.
- Re-run `trivy fs --severity CRITICAL,HIGH,MEDIUM .` to confirm the findings clear.


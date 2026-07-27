# CLI Design Brief

Scope: terminal visual UX only (`dash`, `report`, `stats`). Not a feature
build -- Phase 1-2 (T1-T34) is functionally complete, 99 tests passing, gate
passed 2026-07-20, no blockers. This is a polish pass before calling the
surface done.

Audience: single user (me), daily-use personal tool. Optimize for fast
scanning over decoration. Must read cleanly in both light and dark terminal
themes (no assumptions about background color).

## Current state

Three commands render output, in three different ways:

- **`dash`** (`src/prompt_coach/report/dash.py`) -- full `rich`: `Panel`,
  `Table`, `Columns`, `Text.assemble`. Sparklines via block characters
  (`▁▂▃▄▅▆▇█`, `sparkline()`). Score cells colored green (≥0.7) / yellow
  (≥0.4) / red (below) via `_score_text()`. Layout: header line, then
  `Columns([volume panel, human/machine split panel])` side by side, then a
  full-width scorecard panel, then an optional docs-quality panel appended
  only if flagged findings exist. `--plain` forces `Console(force_terminal=False,
  no_color=True)`; `--no-sync` skips the cache resync.
- **`report`** -- Jinja2 template (`report/templates/report.md.j2`) rendered
  to a markdown string, printed with plain `typer.echo` or written to a file
  via `--out`. No color, no rich -- it's a document, not a screen.
- **`stats`** -- hand-formatted plain-text table via `typer.echo(f"{...:32}
  {...:>10}")`. No `rich` at all, despite `dash` covering overlapping
  metrics (refinement/example/constraint/structured-output rates) with full
  color and layout.

## Inconsistencies to resolve

1. `stats` and `dash` show overlapping data (the human/machine rate table)
   with entirely different visual treatments -- one is a colorless
   fixed-width `echo`, the other a colored `rich.Table`. Pick one convention
   and apply it to both, or justify why `stats` stays deliberately bare
   (it's pitched as the "no LLM needed, quick" command -- maybe bare-plain
   *is* the right call, but that should be a stated decision, not an
   accident of build order).
2. Score-color thresholds (green/yellow/red at 0.7/0.4) exist only in
   `dash`. If `report`'s markdown scorecard or `stats` ever need the same
   semantic, decide now whether color-as-meaning transfers to non-`rich`
   surfaces (e.g. as a text label: "good/fair/weak") rather than being
   silently dropped.
3. `--plain` (dash) and the markdown `report` path both exist to survive
   non-color output, via two different code paths. Confirm that's
   intentional (dash's plain mode is for non-TTY *display*, report's
   markdown is for a *document*) rather than duplicated effort.

## Design principles to apply

- **Scan first, read second.** This is checked most mornings in a terminal,
  not studied. Density should favor sparklines/single-line trends over
  prose; the scorecard table is the one place worth a moment's read.
- **Color carries meaning, never decoration.** Every color in `dash` today
  is semantic (cyan = brand/header, green/yellow/red = score band, dim =
  secondary). Keep it that way -- don't add color for visual interest alone.
- **Degraded states are first-class, not afterthoughts.** The codebase
  already treats "no LLM reachable" and "non-TTY" as real paths
  (`report`'s degraded banner, `dash --plain`, `--no-sync`). The design pass
  should audit that every screen has a deliberate look for: no LLM, no data
  in range, non-TTY, and partial store failure -- not just happy path.
- **No prompt content ever reaches the screen** (see module docstring in
  `dash.py`) -- counts, rates, scores only. Any new visual element must keep
  this invariant; don't let a design idea (e.g. "show example prompts
  inline") violate it.

## Per-screen recommendations to work out

- **`dash` header** -- currently one `Text.assemble` line (name, count,
  session count, since-label). Confirm this scales: what happens at 0
  stores, 1 store, 4 stores? What does it look like on a narrow terminal
  (80 cols)?
- **Volume panel** -- sparkline + total per store, sorted alphabetically
  (`dict(sorted(...))` in `weekly_volumes`). Consider whether store order
  should instead be by volume or by a fixed preferred order (claude-code
  first, since that's primary usage per project scope).
- **Human vs machine panel** -- five rate rows plus prompt count. Check
  whether this is too dense next to the volume panel in a two-column
  `Columns` layout on a standard terminal width, or whether it should stack
  instead of sitting side-by-side.
- **Scorecard** -- full-width table, one row per applicable rubric rule,
  coverage count in a dim column. Decide whether rules with low coverage
  (n=1, n=2) need a visual flag distinct from "n/a" (currently `_score_text`
  only handles `None`, not low-n).
- **Docs-quality panel** -- only appears if there are flagged findings
  (`_docs_panel` returns `None` otherwise). Decide if "no findings" should
  say so explicitly (e.g. "docs: clean") rather than silently omitting the
  panel -- silent omission can read as "didn't run" rather than "passed."

## Edge states to specify explicitly

- First run, no cache yet synced
- `--since` window with zero prompts (currently: `echo` + `Exit(1)` -- is
  that the right tone/wording, or too terse/too alarming for a routine
  "nothing happened this week"?)
- LLM unreachable (`report --no-llm` / desktop Ollama box off) -- `report`
  already has a degraded banner; does `dash` need an equivalent note, given
  it never calls the LLM at all and a user might wonder why?
- One store failing mid-sync (`stats_.stores_failed` exists in `report`;
  confirm `dash` surfaces the same when `--no-sync` isn't used)
- Non-TTY / piped output (`--plain`)

## Out of scope for this pass

- `query`, `discover`, `import`, `cache sync/info/clear`, `nudge` -- plain
  `typer.echo` today, functional, not part of this visual pass unless the
  chosen conventions above naturally extend to them with near-zero effort.
- The `serve` command (not implemented).
- Any new metrics, rubric rules, or analysis logic -- visual treatment of
  existing data only.

"""CLI entry point for prompt-coach."""

from __future__ import annotations

import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from prompt_coach.cache import CacheDB
from prompt_coach.config import Config, load_config
from prompt_coach.models import ReportData

if TYPE_CHECKING:  # never imported at runtime -- see the note below
    from prompt_coach.llm.client import LocalLLM

# Everything else (analysis/*, llm.client, report.generator, stores/*) is
# imported lazily inside the commands/helpers that use it, not here.
# `nudge` runs synchronously on every Claude Code prompt submission (see
# nudge.py's docstring), so cli.py being import-heavy at module scope was a
# real, measured cost: typer must import this whole file to dispatch any
# single command, so `nudge` paid for report/rubric/pattern/store imports
# it never touches -- dominated by the `openai` SDK's own type surface
# (~700-900ms just for `import openai`, see DECISIONS.md 2026-07-29).
# CacheDB/Config/models stay eager: stdlib-only, no measurable cost.

app = typer.Typer(
    name="prompt-coach",
    help="Your personal prompting analyst. Runs locally. Zero data export.",
    no_args_is_help=True,
)
cache_app = typer.Typer(help="Manage the local analysis cache.")
app.add_typer(cache_app, name="cache")

_SINCE = re.compile(r"^(\d+)([dwm])$")


def parse_since(value: str | None) -> datetime | None:
    """'7d' / '4w' / '3m' / ISO date -> UTC datetime."""
    if value is None:
        return None
    m = _SINCE.match(value.strip())
    if m:
        n = int(m.group(1))
        days = {"d": 1, "w": 7, "m": 30}[m.group(2)] * n
        return datetime.now(tz=UTC) - timedelta(days=days)
    try:
        ts = datetime.fromisoformat(value)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    except ValueError as exc:
        raise typer.BadParameter(f"{value!r} is not 7d/4w/3m or an ISO date") from exc


def _since_label(raw: str | None, since_dt: datetime | None) -> str:
    if since_dt is None:
        return "all time"
    date = since_dt.date().isoformat()
    return f"since {date} ({raw})" if raw and raw != date else f"since {date}"


_FIRST_RUN_BODY = "nothing synced -- run `prompt-coach cache sync` to pull your first window."


def _empty_state(cache: CacheDB, since_dt: datetime | None, label: str) -> tuple[str, str]:
    """(header, body) for a no-prompts result. Distinguishes an empty cache (D7:
    two-line pointer to `cache sync`) from a quiet window (D7: calm one-liner) --
    both now exit 0, not 1: neither is an error."""
    if cache.counts()["prompts"] == 0:
        return "no cache yet", _FIRST_RUN_BODY
    return f"0 prompts \u00b7 {label}", f"quiet week -- no prompts {label}. Try a wider --since."


def default_stores(cfg: Config) -> list:
    from prompt_coach.stores.claude_code import ClaudeCodeStore
    from prompt_coach.stores.codex_cli import CodexStore
    from prompt_coach.stores.copilot import CopilotStore
    from prompt_coach.stores.hermes import HermesStore

    candidates = [
        HermesStore(cfg.stores.hermes_db),
        ClaudeCodeStore(cfg.stores.claude_projects_dir),
        CopilotStore(cfg.stores.copilot_dir),
        CodexStore(cfg.stores.codex_dir),
    ]
    # Opt-in: a store present on disk is only used if named in cfg.stores.enabled
    # (DECISIONS.md 2026-07-29).
    return [s for s in candidates if s.kind.value in cfg.stores.enabled]


def open_cache(cfg: Config) -> CacheDB:
    return CacheDB(cfg.cache_dir / "cache.db")


def sync_with_progress(cache: CacheDB, cfg: Config, *, force: bool = False):
    """Sync with a per-store progress bar on stderr, so a slow store (Copilot's
    /mnt/c reads routinely take most of the wall clock) doesn't look like a
    hang between the command starting and the final render appearing. Falls
    back to a silent sync when stderr isn't a terminal (piped/redirected
    output, or under test) -- there's no one to show a bar to."""
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

    stores = default_stores(cfg)
    progress_console = Console(stderr=True)
    if not progress_console.is_terminal:
        return cache.sync(stores, force=force)
    with Progress(
        SpinnerColumn(),
        TextColumn("[dim]syncing {task.fields[store]}[/]"),
        BarColumn(),
        MofNCompleteColumn(),
        console=progress_console,
        transient=True,
    ) as progress:
        task = progress.add_task("sync", total=len(stores), store="")

        def on_store(kind: str, done: bool) -> None:
            if done:
                progress.advance(task)
            else:
                progress.update(task, store=kind)

        return cache.sync(stores, force=force, on_store=on_store)


def make_llm(cfg: Config) -> LocalLLM | None:
    """LocalLLM when the endpoint is reachable, else None (degraded mode)."""
    from prompt_coach.config import APIKeyCommandFailed
    from prompt_coach.llm.client import LocalLLM, RemoteEndpointRefused

    try:
        api_key = cfg.llm.resolve_api_key()
    except APIKeyCommandFailed as exc:
        typer.echo(f"LLM disabled: {exc}", err=True)
        return None
    try:
        llm = LocalLLM(
            cfg.llm.base_url,
            cfg.llm.model,
            api_key=api_key,
            timeout=cfg.llm.timeout,
            allow_remote=cfg.llm.allow_remote,
            reasoning=cfg.llm.reasoning,
        )
    except RemoteEndpointRefused as exc:
        typer.echo(f"Refusing remote endpoint: {exc}", err=True)
        raise typer.Exit(2) from exc
    return llm if llm.available() else None


@app.command()
def discover():
    """Find all available session stores on this machine."""
    cfg = load_config()
    for store in default_stores(cfg):
        info = store.discover()
        status = "available" if info.available else f"unavailable ({info.detail})"
        counts = ""
        if info.available:
            parts = []
            if info.session_count is not None:
                parts.append(f"{info.session_count} sessions")
            if info.prompt_count is not None:
                parts.append(f"{info.prompt_count} prompts")
            counts = f"  [{', '.join(parts)}]" if parts else ""
        typer.echo(f"{info.kind.value:12} {str(info.path):45} {status}{counts}")


def _claude_home() -> Path:
    """~/.claude, overridable for tests (PROMPT_COACH_CLAUDE_HOME) the same
    way config.py's store paths are -- doctor/migrate-legacy read and write
    settings.json/commands/ directly, which is a different concern from
    config.py's Config (that's prompt-coach's own config; this is Claude
    Code's), so it gets its own env var rather than joining that schema."""
    override = os.environ.get("PROMPT_COACH_CLAUDE_HOME")
    return Path(override).expanduser() if override else Path.home() / ".claude"


@app.command()
def doctor(json_format: bool = typer.Option(False, "--json", help="Output as JSON")):
    """Read-only report on the Claude Code plugin: is `claude` on PATH, is
    the plugin installed, and are the legacy hand-edited hooks/commands it
    replaces still present (running both at once double-fires every
    prompt). Never writes anything -- see `migrate-legacy` to act on it."""
    import shutil as _shutil

    from prompt_coach.plugin import find_repo_root, is_plugin_enabled, plan_migration

    claude_home = _claude_home()
    claude_bin = _shutil.which("claude")
    repo_root = find_repo_root()
    plugin_enabled = is_plugin_enabled(claude_home)
    plan = plan_migration(claude_home)
    double_fire = plugin_enabled and plan.has_legacy_hooks

    if json_format:
        typer.echo(
            _json_dumps(
                {
                    "claude_cli": claude_bin,
                    "repo_root": str(repo_root) if repo_root else None,
                    "plugin_enabled": plugin_enabled,
                    "legacy_hooks": [
                        {"event": r.event, "command": r.command} for r in plan.hook_removals
                    ],
                    "legacy_commands": [str(p) for p in plan.legacy_command_files],
                    "double_fire_risk": double_fire,
                }
            )
        )
        return

    typer.echo("prompt-coach plugin doctor\n")
    typer.echo(f"claude CLI:      {'found at ' + claude_bin if claude_bin else 'NOT on PATH'}")
    typer.echo(f"plugin repo:     {repo_root if repo_root else 'not found (pass --repo by hand)'}")
    typer.echo(f"plugin enabled:  {'yes' if plugin_enabled else 'no'}")
    typer.echo(
        f"legacy hooks:    {len(plan.hook_removals)} found"
        + (f" ({', '.join(r.event for r in plan.hook_removals)})" if plan.hook_removals else "")
    )
    typer.echo(
        f"legacy commands: {len(plan.legacy_command_files)} found"
        + (
            f" ({', '.join(p.name for p in plan.legacy_command_files)})"
            if plan.legacy_command_files
            else ""
        )
    )
    typer.echo("")
    if double_fire:
        typer.echo(
            "! Both the plugin and the legacy hooks are active -- every prompt is "
            "nudged twice. Run `prompt-coach migrate-legacy` to remove the old ones."
        )
    elif not plugin_enabled:
        typer.echo("Plugin not installed. Run `prompt-coach migrate-legacy` to install it.")
    elif plan.needs_migration:
        typer.echo(
            "Plugin installed; some legacy files remain. Run `prompt-coach migrate-legacy` "
            "to clean them up."
        )
    else:
        typer.echo("All good -- plugin installed, no legacy hooks or commands left.")


def _json_dumps(data: dict) -> str:
    import json as _json

    return _json.dumps(data, indent=2)


@app.command("migrate-legacy")
def migrate_legacy(
    repo: str | None = typer.Option(
        None, "--repo", help="Path to the prompt-coach repo (auto-detected via uv if omitted)"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the plan; change nothing"),
):
    """Install the Claude Code plugin (if not already) and remove the
    legacy hand-edited hooks/commands it replaces. Always shows the plan
    and confirms before writing anything; backs up settings.json and every
    legacy command file it touches first (see plugin.py's apply_* docs)."""
    from click.exceptions import Abort

    from prompt_coach.plugin import (
        find_repo_root,
        is_plugin_enabled,
        is_valid_plugin_repo,
        plan_migration,
    )

    claude_home = _claude_home()
    repo_root = Path(repo).expanduser() if repo else find_repo_root()
    plugin_enabled = is_plugin_enabled(claude_home)
    plan = plan_migration(claude_home)
    need_install = not plugin_enabled

    if not need_install and not plan.needs_migration:
        typer.echo("Nothing to do -- plugin already installed, no legacy hooks or commands found.")
        return

    typer.echo("prompt-coach migrate-legacy\n")
    typer.echo("This will:")
    if need_install:
        if repo_root is None:
            typer.echo(
                "  - install the plugin -- but the repo location couldn't be auto-detected.\n"
                "    Re-run with --repo /path/to/prompt-coach."
            )
            raise typer.Exit(1)
        if not is_valid_plugin_repo(repo_root):
            typer.echo(f"  ! {repo_root} doesn't look like the prompt-coach repo (no plugin/).")
            raise typer.Exit(1)
        typer.echo(f"  - install the plugin from {repo_root}")
    for removal in plan.hook_removals:
        typer.echo(f"  - remove the legacy {removal.event} hook ({removal.command})")
    for path in plan.legacy_command_files:
        typer.echo(f"  - back up and remove {path}")
    typer.echo("")

    if dry_run:
        typer.echo("(--dry-run: nothing changed)")
        return

    if not yes:
        try:
            if not typer.confirm("Proceed?", default=False):
                typer.echo("Cancelled -- nothing changed.")
                return
        except Abort:
            # EOF on the prompt (no terminal): match setup's convention --
            # cancelled, not a failure.
            typer.echo(
                "\nmigrate-legacy stopped early -- it asks for confirmation, so it needs a "
                "real terminal. Run `prompt-coach migrate-legacy` in a shell."
            )
            raise typer.Exit(0) from None

    if not _execute_migration(claude_home, repo_root, plan, need_install):
        raise typer.Exit(1)


def _execute_migration(claude_home: Path, repo_root: Path | None, plan, need_install: bool) -> bool:
    """The write side of migrate-legacy, shared with setup's plugin-install
    offer below: install (if needed), then remove whatever legacy hooks/
    commands the plan found. Assumes the plan was already shown and
    confirmed. Returns False (install failed, nothing else touched) or True.

    `plan` is only used to decide *whether* there's anything to remove and
    to report counts -- the removal itself always re-reads settings.json
    fresh via plan_migration() right before writing. `claude plugin
    install` (above) writes its own keys (enabledPlugins,
    extraKnownMarketplaces) into that same file; a stale plan captured
    before install ran would silently clobber that write when applied
    after. Caught live 2026-09-24 running this end-to-end against a real
    `claude` binary, not by the mocked-install unit tests -- see
    tests/test_cli.py's test_migrate_legacy_installs_plugin_when_missing,
    which now makes its install mock write to the file the same way, to
    guard the fix."""
    from prompt_coach.plugin import (
        ClaudeCLIError,
        apply_hook_removal,
        backup_and_remove_legacy_commands,
        install_plugin,
        plan_migration,
    )

    if need_install:
        try:
            install_plugin(repo_root)
        except ClaudeCLIError as exc:
            typer.echo(f"Plugin install failed, nothing else touched: {exc}", err=True)
            return False
        typer.echo(f"Installed the plugin from {repo_root}.")

    if plan.has_legacy_hooks or plan.has_legacy_commands:
        plan = plan_migration(claude_home)

    if plan.has_legacy_hooks:
        backup = apply_hook_removal(claude_home / "settings.json", plan)
        typer.echo(f"Removed {len(plan.hook_removals)} legacy hook(s) (backup: {backup}).")

    if plan.has_legacy_commands:
        backups = backup_and_remove_legacy_commands(plan)
        for path, backup in zip(plan.legacy_command_files, backups, strict=True):
            typer.echo(f"Removed {path.name} (backup: {backup}).")

    typer.echo(
        "\nDone. Running Claude Code sessions won't pick this up until they restart "
        "or run /reload-plugins."
    )
    return True


@app.command()
def setup():
    """Interactive setup wizard: choose which stores are active, set the LLM
    endpoint/model, nudge mode, model-fit mode, and optionally a per-directory
    nudge override -- writes ~/.config/prompt-coach/config.toml. Ends by
    offering to run a report or open the dashboard."""
    from click.exceptions import Abort

    try:
        _setup_wizard()
    except Abort:
        # EOF on a question (no terminal -- e.g. run via the /prompt-coach
        # slash command, seen live 2026-07-31) or Ctrl-C. Either way it's a
        # cancelled wizard, not a failure: exit 0 with a pointer, matching
        # the first-run-isn't-an-error convention.
        typer.echo(
            "\nsetup stopped early -- it asks questions interactively, so it "
            "needs a real terminal. Run `prompt-coach setup` in a shell "
            "(not via /prompt-coach)."
        )
        raise typer.Exit(0) from None


def _offer_plugin_setup() -> None:
    """Called once near the end of the setup wizard: offers to install the
    Claude Code plugin and/or clean up the legacy hooks/commands it
    replaces, reusing migrate-legacy's own plan + execute (see
    _execute_migration above) so the two entry points never drift. Silent
    when there's nothing to do, so re-running setup later doesn't nag."""
    from click.exceptions import Abort

    from prompt_coach.plugin import (
        find_repo_root,
        is_plugin_enabled,
        is_valid_plugin_repo,
        plan_migration,
    )

    claude_home = _claude_home()
    plugin_enabled = is_plugin_enabled(claude_home)
    plan = plan_migration(claude_home)
    need_install = not plugin_enabled
    if not need_install and not plan.needs_migration:
        return

    typer.echo("\nClaude Code plugin:")
    if need_install:
        typer.echo(
            "  not installed -- this gives you the nudge hooks and /prompt-coach:* commands."
        )
    if plan.has_legacy_hooks or plan.has_legacy_commands:
        typer.echo(
            f"  {len(plan.hook_removals)} legacy hook(s) and {len(plan.legacy_command_files)} "
            "legacy command file(s) found from the old hand-edited setup."
        )

    repo_root = find_repo_root()
    if need_install and (repo_root is None or not is_valid_plugin_repo(repo_root)):
        typer.echo(
            "  Couldn't auto-detect the repo location -- run `prompt-coach migrate-legacy "
            "--repo /path/to/prompt-coach` by hand later."
        )
        return

    try:
        do_it = typer.confirm("  Set it up now?", default=True)
    except Abort:
        return
    if not do_it:
        typer.echo("  Skipped -- run `prompt-coach migrate-legacy` any time.")
        return
    _execute_migration(claude_home, repo_root, plan, need_install)


def _setup_wizard():
    import dataclasses

    from prompt_coach.config import (
        MODEL_FIT_MODES,
        NUDGE_MODES,
        APIKeyCommandFailed,
        Config,
        ModelFitConfig,
        NudgeConfig,
        StoresConfig,
        config_file_path,
        write_config,
    )
    from prompt_coach.llm.client import LocalLLM, RemoteEndpointRefused
    from prompt_coach.stores.claude_code import ClaudeCodeStore
    from prompt_coach.stores.codex_cli import CodexStore
    from prompt_coach.stores.copilot import CopilotStore
    from prompt_coach.stores.hermes import HermesStore

    cfg = load_config()
    typer.echo("prompt-coach setup\n")

    typer.echo("Which stores should be active?")
    stores_by_kind = {
        "hermes": HermesStore(cfg.stores.hermes_db),
        "claude-code": ClaudeCodeStore(cfg.stores.claude_projects_dir),
        "copilot": CopilotStore(cfg.stores.copilot_dir),
        "codex": CodexStore(cfg.stores.codex_dir),
    }
    enabled: set[str] = set()
    for kind, store in stores_by_kind.items():
        info = store.discover()
        was_enabled = kind in cfg.stores.enabled
        if info.available:
            detail = f"found, {info.session_count} sessions" if info.session_count else "found"
        else:
            detail = f"not found ({info.detail})"
        if typer.confirm(f"  {kind} -- {detail}. Enable?", default=info.available and was_enabled):
            enabled.add(kind)

    typer.echo("\nLLM endpoint (local-only; a public URL needs allow_remote set by hand):")
    base_url = typer.prompt("  base URL", default=cfg.llm.base_url)
    model = typer.prompt("  model", default=cfg.llm.model)
    try:
        probe = LocalLLM(
            base_url,
            model,
            api_key=cfg.llm.resolve_api_key(),
            timeout=2.0,
            allow_remote=cfg.llm.allow_remote,
        )
        reachable = probe.available()
    except (RemoteEndpointRefused, APIKeyCommandFailed) as exc:
        typer.echo(f"  refused: {exc}", err=True)
        reachable = False
    typer.echo(f"  -> {'reachable now' if reachable else 'not reachable right now'}")

    nudge_mode = typer.prompt(f"\nNudge mode ({'/'.join(NUDGE_MODES)})", default=cfg.nudge.mode)
    while nudge_mode not in NUDGE_MODES:
        typer.echo(f"  must be one of {NUDGE_MODES}", err=True)
        nudge_mode = typer.prompt("Nudge mode", default=cfg.nudge.mode)

    model_fit_mode = typer.prompt(
        f"Model-fit mode ({'/'.join(MODEL_FIT_MODES)})", default=cfg.model_fit.mode
    )
    while model_fit_mode not in MODEL_FIT_MODES:
        typer.echo(f"  must be one of {MODEL_FIT_MODES}", err=True)
        model_fit_mode = typer.prompt("Model-fit mode", default=cfg.model_fit.mode)

    dir_overrides = dict(cfg.nudge.dir_overrides)
    here = str(Path.cwd())
    if typer.confirm(f"\nOverride nudge mode just for this directory ({here})?", default=False):
        override_mode = typer.prompt(f"  nudge mode for {here}", default=nudge_mode)
        while override_mode not in NUDGE_MODES:
            typer.echo(f"  must be one of {NUDGE_MODES}", err=True)
            override_mode = typer.prompt("  nudge mode", default=nudge_mode)
        dir_overrides[here] = override_mode

    new_cfg = Config(
        llm=dataclasses.replace(cfg.llm, base_url=base_url, model=model),
        stores=StoresConfig(
            hermes_db=cfg.stores.hermes_db,
            claude_projects_dir=cfg.stores.claude_projects_dir,
            copilot_dir=cfg.stores.copilot_dir,
            codex_dir=cfg.stores.codex_dir,
            enabled=frozenset(enabled),
        ),
        nudge=NudgeConfig(
            mode=nudge_mode, llm_timeout=cfg.nudge.llm_timeout, dir_overrides=dir_overrides
        ),
        model_fit=ModelFitConfig(mode=model_fit_mode),
        cache_dir=cfg.cache_dir,
    )
    path = config_file_path()
    write_config(new_cfg, path)
    typer.echo(f"\nSaved to {path}")

    _offer_plugin_setup()

    if typer.confirm("\nRun a report now?", default=True):
        report(since=None, limit=None, sample=150, no_llm=False, refresh=False, out=None)
    elif typer.confirm("Open the dashboard instead?", default=False):
        dash(since="12w", plain=False, no_sync=False)


@app.command()
def stats(
    since: str | None = typer.Option(None, "--since", help="Time range (e.g. 7d, 30d)"),
    plain: bool = typer.Option(False, "--plain", help="Force plain text output"),
):
    """Quick overview of prompting metrics (deterministic, no LLM)."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from prompt_coach.analysis.metrics import compute_metrics

    cfg = load_config()
    cache = open_cache(cfg)
    sync_stats = sync_with_progress(cache, cfg)
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    console = Console(force_terminal=False, no_color=True) if plain else Console()
    label = _since_label(since, since_dt)
    if not prompts:
        header, body = _empty_state(cache, since_dt, label)
        console.print(Text.assemble(("prompt-coach", "bold cyan"), (f" \u00b7 {header}", "dim")))
        console.print(Text(body, style="dim"))
        return
    for store, reason in sync_stats.stores_failed.items():
        console.print(Text(f"\u26a0 {store}: sync failed ({reason})", style="yellow"))
    m = compute_metrics(prompts)
    # Rates are behavior, not quality -- no score-band colors here (D1).
    console.print(
        Text.assemble(
            ("prompt-coach stats", "bold cyan"),
            (f" \u00b7 {label} \u00b7 ", "dim"),
            ("no LLM", "dim"),
        )
    )
    table = Table(box=None)
    table.add_column("Metric", style="bold")
    table.add_column("Human", justify="right")
    table.add_column("Machine", justify="right")
    rows = [
        ("Prompts", "prompt_count", "{:d}"),
        ("Sessions", "session_count", "{:d}"),
        ("Avg est. tokens", "avg_prompt_tokens", "{:.0f}"),
        ("Median est. tokens", "median_prompt_tokens", "{:.0f}"),
        ("Prompts per session", "prompts_per_session", "{:.1f}"),
        ("Refinement rate", "refinement_rate", "{:.0%}"),
        ("Example rate", "example_rate", "{:.0%}"),
        ("Constraint rate", "constraint_rate", "{:.0%}"),
        ("Structured-output rate", "structured_output_rate", "{:.0%}"),
    ]
    for row_label, attr, fmt in rows:
        h = fmt.format(getattr(m.human, attr)) if m.human else "-"
        mc = fmt.format(getattr(m.machine, attr)) if m.machine else "-"
        table.add_row(row_label, h, mc)
    console.print(table)


@app.command()
def report(
    since: str | None = typer.Option(None, "--since", help="Time range (e.g. 7d, 30d)"),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max prompts to analyse"),
    sample: int = typer.Option(150, "--sample", help="LLM rubric sample size"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Deterministic analysis only"),
    refresh: bool = typer.Option(False, "--refresh", help="Force full store resync"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write report to file"),
):
    """Generate a coaching report from your prompt history."""
    from prompt_coach.analysis.metrics import compute_metrics
    from prompt_coach.analysis.model_fit import detect_mismatches
    from prompt_coach.analysis.patterns import detect_patterns
    from prompt_coach.analysis.rubric import run_rubric
    from prompt_coach.llm.client import LLMUnavailable
    from prompt_coach.report.generator import build_report
    from prompt_coach.stores.hermes import HermesStore

    cfg = load_config()
    cache = open_cache(cfg)
    stats_ = sync_with_progress(cache, cfg, force=refresh)
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt, limit=limit)
    if not prompts:
        # A quiet window or an unsynced cache are routine, not failures (D7).
        _, body = _empty_state(cache, since_dt, _since_label(since, since_dt))
        typer.echo(f"# Prompt Coach Report - {datetime.now(tz=UTC).date().isoformat()}")
        typer.echo()
        typer.echo(body)
        return

    llm = None if no_llm else make_llm(cfg)
    rubric = run_rubric(prompts, llm, cache, sample_size=sample)
    patterns = None
    if llm is not None:
        try:
            patterns = detect_patterns(prompts, llm, cache)
        except LLMUnavailable:
            llm = None  # model died mid-run: banner the report honestly

    data = ReportData(
        generated_at=datetime.now(tz=UTC),
        since=since_dt,
        prompt_count=len(prompts),
        session_count=len({f"{p.source}:{p.session_id}" for p in prompts}),
        store_counts={
            k: v
            for k, v in cache.counts(since=since_dt).items()
            if k not in ("prompts", "sessions")
        },
        skipped_stores=stats_.stores_failed,
        metrics=compute_metrics(prompts),
        rubric=rubric,
        patterns=patterns,
        llm_available=llm is not None,
        llm_model=cfg.llm.model if llm is not None else None,
        session_titles=tuple(HermesStore(cfg.stores.hermes_db).session_titles(since_dt)[:30]),
        model_fit=detect_mismatches(prompts, cfg.model_fit.mode),
    )
    text = build_report(data)
    if out:
        out.write_text(text)
        typer.echo(f"Report written to {out}")
    else:
        typer.echo(text)


@app.command()
def dash(
    since: str | None = typer.Option("12w", "--since", help="Time range (default 12w)"),
    plain: bool = typer.Option(False, "--plain", help="Force plain text output"),
    no_sync: bool = typer.Option(
        False, "--no-sync", help="Skip store sync, render from cache as-is"
    ),
    html: Path | None = typer.Option(
        None, "--html", help="Write the dashboard to a self-contained HTML file instead of stdout"
    ),
):
    """Terminal dashboard: volume trends, segments, rubric scores. No LLM, no content."""
    from rich.console import Console
    from rich.text import Text

    from prompt_coach.analysis.docs import analyse_docs
    from prompt_coach.analysis.metrics import compute_metrics
    from prompt_coach.analysis.model_fit import detect_mismatches
    from prompt_coach.analysis.rubric import run_rubric
    from prompt_coach.report.dash import build_dash, weekly_volumes

    cfg = load_config()
    cache = open_cache(cfg)
    stores_failed: dict[str, str] = {}
    if not no_sync:
        stores_failed = sync_with_progress(cache, cfg).stores_failed
    # --html renders the same dashboard to a standalone page instead of the
    # terminal; the shared renderer is also what `serve` responds with.
    if html is not None:
        html.write_text(
            _render_dash_html(cfg, cache, since, stores_failed=stores_failed), encoding="utf-8"
        )
        typer.echo(f"Wrote {html}", err=True)
        return
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    console = Console(force_terminal=False, no_color=True) if plain else Console()
    label = _since_label(since, since_dt)
    if not prompts:
        header, body = _empty_state(cache, since_dt, label)
        console.print(Text.assemble(("prompt-coach", "bold cyan"), (f" \u00b7 {header}", "dim")))
        console.print(Text(body, style="dim"))
        return
    for store, reason in stores_failed.items():
        console.print(Text(f"\u26a0 {store}: sync failed ({reason})", style="yellow"))
    store_counts = {
        k: v for k, v in cache.counts(since=since_dt).items() if k not in ("prompts", "sessions")
    }
    renderable = build_dash(
        metrics=compute_metrics(prompts),
        rubric=run_rubric(prompts, None, cache),
        volumes=weekly_volumes(prompts),
        prompt_count=len(prompts),
        session_count=len({f"{p.source}:{p.session_id}" for p in prompts}),
        since_label=label,
        store_count=len(store_counts),
        stale_count=len(stores_failed),
        docs=analyse_docs(prompts),
        model_fit=detect_mismatches(prompts, cfg.model_fit.mode),
        width=console.size.width,
        plain=plain,
    )
    console.print(renderable)


def _render_dash_html(
    cfg: Config, cache: CacheDB, since: str | None, *, stores_failed: dict[str, str] | None = None
) -> str:
    """Render the dashboard from the cache as-is and return a self-contained HTML
    page (inline styles, no external assets -- opens straight from disk). Does not
    sync: the caller decides when to refresh the cache. Reuses rich's exporter, so
    the page carries no prompt content the terminal dash wouldn't. Shared by
    `dash --html` (write to file) and `serve` (respond per request)."""
    import io

    from rich.console import Console
    from rich.terminal_theme import MONOKAI
    from rich.text import Text

    from prompt_coach.analysis.docs import analyse_docs
    from prompt_coach.analysis.metrics import compute_metrics
    from prompt_coach.analysis.model_fit import detect_mismatches
    from prompt_coach.analysis.rubric import run_rubric
    from prompt_coach.report.dash import DASH_HTML_FORMAT, HTML_WIDTH, build_dash, weekly_volumes

    stores_failed = stores_failed or {}
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    label = _since_label(since, since_dt)
    # Fixed-width color console backed by an in-memory buffer, not stdout.
    console = Console(record=True, width=HTML_WIDTH, force_terminal=True, file=io.StringIO())
    if not prompts:
        header, body = _empty_state(cache, since_dt, label)
        console.print(Text.assemble(("prompt-coach", "bold cyan"), (f" · {header}", "dim")))
        console.print(Text(body, style="dim"))
    else:
        for store, reason in stores_failed.items():
            console.print(Text(f"⚠ {store}: sync failed ({reason})", style="yellow"))
        store_counts = {
            k: v
            for k, v in cache.counts(since=since_dt).items()
            if k not in ("prompts", "sessions")
        }
        console.print(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=run_rubric(prompts, None, cache),
                volumes=weekly_volumes(prompts),
                prompt_count=len(prompts),
                session_count=len({f"{p.source}:{p.session_id}" for p in prompts}),
                since_label=label,
                store_count=len(store_counts),
                stale_count=len(stores_failed),
                docs=analyse_docs(prompts),
                model_fit=detect_mismatches(prompts, cfg.model_fit.mode),
                width=HTML_WIDTH,
                plain=False,
            )
        )
    generated = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    html_text = console.export_html(theme=MONOKAI, inline_styles=True, code_format=DASH_HTML_FORMAT)
    return html_text.replace("%%GENERATED%%", generated)


@app.command()
def query(
    question: str = typer.Argument(help="Question about your prompt history"),
):
    """Ask a natural-language question about your prompt history."""
    from prompt_coach.query import answer

    cfg = load_config()
    cache = open_cache(cfg)
    sync_with_progress(cache, cfg)
    typer.echo(answer(question, cache, make_llm(cfg)))


@app.command("import")
def import_(
    file: Path = typer.Argument(help="Path to a JSON/ZIP export (ChatGPT or simple format)"),
):
    """Import external session data into the cache (format auto-detected)."""
    import json as _json

    from prompt_coach.stores.chatgpt_export import ChatGPTExportStore, looks_like_chatgpt_export
    from prompt_coach.stores.json_import import JsonImportStore

    cfg = load_config()
    store: ChatGPTExportStore | JsonImportStore
    if file.suffix.lower() == ".zip":
        store = ChatGPTExportStore(file)
    else:
        try:
            with open(file) as f:
                data = _json.load(f)
        except (OSError, _json.JSONDecodeError) as exc:
            typer.echo(f"Cannot read {file}: {type(exc).__name__}", err=True)
            raise typer.Exit(1) from exc
        if looks_like_chatgpt_export(data):
            store = ChatGPTExportStore(file)
        else:
            store = JsonImportStore(file)
    info = store.discover()
    if not info.available:
        typer.echo(f"Cannot read {file}: {info.detail}", err=True)
        raise typer.Exit(1)
    cache = open_cache(cfg)
    s = cache.sync([store])
    typer.echo(
        f"Imported {s.added} prompts ({s.deduped} already present)"
        f" from {file} as {store.kind.value}"
    )


@cache_app.command("sync")
def cache_sync(
    refresh: bool = typer.Option(False, "--refresh", help="Ignore watermarks, rescan all"),
):
    """Sync all stores into the local cache."""
    cfg = load_config()
    s = sync_with_progress(open_cache(cfg), cfg, force=refresh)
    typer.echo(f"Scanned {s.scanned}, added {s.added}, deduped {s.deduped}")
    for store, reason in s.stores_failed.items():
        typer.echo(f"Skipped {store}: {reason}")


@cache_app.command("info")
def cache_info():
    """Show cache contents (counts only, never prompt text)."""
    cfg = load_config()
    cache = open_cache(cfg)
    counts = cache.counts()
    typer.echo(f"Cache: {cfg.cache_dir / 'cache.db'}")
    for key, value in counts.items():
        typer.echo(f"  {key}: {value}")
    typer.echo(f"  llm cache entries: {cache.llm_cache_stats()['entries']}")


@cache_app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete all cached prompts and LLM results (source stores untouched)."""
    if not yes and not typer.confirm("Delete all cached analysis data?"):
        raise typer.Exit(0)
    cfg = load_config()
    open_cache(cfg).clear()
    typer.echo("Cache cleared.")


@app.command()
def audit(
    since: str | None = typer.Option(None, "--since", help="Time range (e.g. 7d, 30d)"),
    mode: str | None = typer.Option(
        None, "--mode", "-m", help="Filter by mode (auto, coach, always, off)"
    ),
    detail: bool = typer.Option(
        False, "--detail", "-d", help="Show individual audit entries with before/after"
    ),
    limit: int = typer.Option(20, "--limit", "-l", help="Max entries to show (detail mode)"),
    plain: bool = typer.Option(False, "--plain", help="Force plain text output"),
    json_format: bool = typer.Option(False, "--json", help="Output as JSON for agent consumption"),
):
    """View the nudge audit trail: interventions across all modes.

    Shows a summary table by default. Use --detail for individual entries
    with original and refined prompts.
    """
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    cfg = load_config()
    cache = open_cache(cfg)
    since_dt = parse_since(since)
    label = _since_label(since, since_dt)
    console = Console(force_terminal=False, no_color=True) if plain else Console()

    if json_format:
        import json as _json

        if detail:
            events = cache.get_audit_events(since=since_dt, mode=mode, limit=limit)
            typer.echo(_json.dumps(events))
        else:
            summary = cache.audit_summary(since=since_dt, mode=mode)
            typer.echo(_json.dumps(summary))
        return

    if detail:
        events = cache.get_audit_events(since=since_dt, mode=mode, limit=limit)
        if not events:
            header = f"no audit events{mode and f' ({mode})' or ''} \u00b7 {label}"
            console.print(
                Text.assemble(("prompt-coach audit", "bold cyan"), (f" \u00b7 {header}", "dim"))
            )
            return
        console.print(
            Text.assemble(
                ("prompt-coach audit", "bold cyan"),
                (f" \u00b7 {label} \u00b7 {len(events)} events", "dim"),
            )
        )
        for e in events:
            ts = datetime.fromtimestamp(e["timestamp"], tz=UTC)
            trig = e["trigger"] or ""
            mode_str = e["mode"]
            orig_len = e["original_length"]
            ref_len = e["refined_length"]
            saved = (orig_len - ref_len) if ref_len else 0
            tokens_saved = e["token_estimate"] - (e["refined_token_estimate"] or 0)
            console.print()
            console.print(
                Text.assemble(
                    (f"[{ts.date().isoformat()} {ts.strftime('%H:%M:%S')}]", "bold"),
                    (f"  {mode_str}", "cyan"),
                    (f"  {e['hook_event'] or ''}", "dim"),
                )
            )
            console.print(f"  Trigger: {trig}")
            console.print(f"  Session: {e['session_id']}")
            console.print(
                f"  Length: {orig_len} chars -> {ref_len or '?'} chars"
                f" ({saved} saved, ~{tokens_saved} tokens)"
            )
            console.print(f"  Original: {e['original_prompt'][:200]}")
            if e["refined_prompt"]:
                console.print(f"  Refined:  {e['refined_prompt'][:200]}")
    else:
        summary = cache.audit_summary(since=since_dt, mode=mode)
        if not summary:
            header = f"no audit events{mode and f' ({mode})' or ''} \u00b7 {label}"
            console.print(
                Text.assemble(("prompt-coach audit", "bold cyan"), (f" \u00b7 {header}", "dim"))
            )
            return
        console.print(
            Text.assemble(
                ("prompt-coach audit", "bold cyan"),
                (f" \u00b7 {label}", "dim"),
            )
        )
        table = Table(box=None)
        table.add_column("Mode", style="bold")
        table.add_column("Interventions", justify="right")
        table.add_column("Avg tokens saved", justify="right")
        table.add_column("Total tokens saved", justify="right")
        table.add_column("Rewrites", justify="right")
        for mode_key, data in sorted(summary.items()):
            table.add_row(
                mode_key,
                str(data["interventions"]),
                f"{data['avg_savings']:.0f}",
                str(data["total_savings"]),
                str(data["rewrites"]),
            )
        console.print(table)


@app.command()
def nudge():
    """Hook target for Claude Code (UserPromptSubmit + Stop) and Hermes
    (pre_llm_call shell hook, tip-only -- see nudge.py's module docstring):
    prints a hook-response JSON to stdout, dispatching on the payload's
    hook_event_name. Mode ("coach"/"auto"/"always"/"off") comes from config
    -- see nudge.py's module docstring. No store sync. In Claude Code's
    "coach"/"always" modes this may call the local LLM (bounded by its own
    short timeout, not the 120s default) and block the prompt; "auto" is
    LLM-free and injects a self-improve directive instead. Must never raise
    -- a hook failure would block every prompt or response."""
    import json as _json

    from prompt_coach.nudge import build_response

    try:
        payload = _json.load(sys.stdin)
        cfg = load_config()
        response = build_response(payload, cfg)
    except Exception:
        response = {}
    typer.echo(_json.dumps(response))


@app.command()
def score(
    text: str = typer.Argument(help="The prompt text to score (0-100)"),
    json_format: bool = typer.Option(False, "--json", help="Output as JSON for agent consumption"),
):
    """Score a prompt from 0-100 based on deterministic quality signals.

    Evaluates structure, clarity, scope, constraints, and examples.
    Pure deterministic — no LLM call, instant.
    """
    import json as _json

    from prompt_coach.analysis.score import score_breakdown

    result = score_breakdown(text)
    if json_format:
        typer.echo(_json.dumps(result))
    else:
        typer.echo(f"Score: {result['score']}/100")
        if result["bonuses"]:
            typer.echo("\nBonuses:")
            for label, pts in result["bonuses"]:
                typer.echo(f"  +{pts}  {label}")
        if result["penalties"]:
            typer.echo("\nPenalties:")
            for label, pts in result["penalties"]:
                typer.echo(f"  {pts}  {label}")
        if not result["bonuses"] and not result["penalties"]:
            typer.echo("  No significant signals detected.")


@app.command("streaks")
def streaks_cmd(
    json_format: bool = typer.Option(False, "--json", help="Output as JSON for agent consumption"),
):
    """Show current streak data: good prompt streak, no-intervention streak,
    and best ever streak."""
    import json as _json

    from prompt_coach.streaks import get_streaks

    cfg = load_config()
    data = get_streaks(cfg.cache_dir)
    if json_format:
        typer.echo(_json.dumps(data))
    else:
        typer.echo(f"Good prompt streak: {data.get('good_prompt_streak', 0)}")
        typer.echo(f"No-intervention streak: {data.get('no_intervention_streak', 0)}")
        typer.echo(f"Best ever streak: {data.get('best_streak', 0)}")


@app.command("nudge-consume")
def nudge_consume(
    want: str = typer.Option(..., "--want", help="'rewritten' or 'original'"),
    session: str = typer.Option(..., "--session", help="Claude Code session ID"),
):
    """Internal target for the /prompt-coach:accept and /prompt-coach:original
    plugin skills (plugin/skills/, not part of this package): pops the
    rewrite `nudge` held onto for SESSION and prints the requested side of
    it, so the slash command's bash substitution can resend it as the next
    message. Always exits 0 -- its stdout is spliced straight into the next
    prompt, so a failure should read as a note, not break the substitution."""
    from prompt_coach.nudge import consume_pending_rewrite

    cfg = load_config()
    text = consume_pending_rewrite(cfg.cache_dir, session, want)
    if text is None:
        typer.echo("(no prompt-coach rewrite is pending for this session)")
        return
    typer.echo(text)


def _build_dash_server(
    cfg: Config, host: str, port: int, since: str | None, stores_failed: dict[str, str]
):
    """Build (but don't start) an HTTPServer that renders the dashboard on each GET
    of `/`. Split out from `serve` so it can be started, hit, and shut down under
    test without blocking. Re-opens the cache per request and re-renders from it, so
    a browser refresh reflects whatever is in the cache now (no re-sync per hit)."""
    import http.server

    class _DashHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib-defined name)
            if self.path.split("?", 1)[0] not in ("/", "/index.html"):
                self.send_error(404)
                return
            try:
                page = _render_dash_html(
                    cfg, open_cache(cfg), since, stores_failed=stores_failed
                ).encode("utf-8")
            except Exception as exc:  # one bad render must not kill the server
                self.send_error(500, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *args):  # silence per-request stderr access logs
            pass

    return http.server.HTTPServer((host, port), _DashHandler)


@app.command()
def serve(
    since: str | None = typer.Option("12w", "--since", help="Time range (default 12w)"),
    port: int = typer.Option(0, "--port", help="Port to bind (default: an open one)"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser"),
    no_sync: bool = typer.Option(
        False, "--no-sync", help="Skip the startup store sync, serve from cache as-is"
    ),
):
    """Serve the dashboard on 127.0.0.1 and open it in a browser. Localhost only --
    nothing leaves the machine. Syncs once at startup (unless --no-sync); after that
    each page load re-renders from the cache, so refresh is instant. Ctrl-C to stop."""
    import webbrowser

    cfg = load_config()
    stores_failed: dict[str, str] = {}
    if not no_sync:
        stores_failed = sync_with_progress(open_cache(cfg), cfg).stores_failed
    try:
        server = _build_dash_server(cfg, "127.0.0.1", port, since, stores_failed)
    except OSError as exc:
        typer.echo(f"Cannot bind 127.0.0.1:{port} ({exc}).", err=True)
        raise typer.Exit(1) from exc
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    typer.echo(f"prompt-coach dashboard at {url}  (Ctrl-C to stop)", err=True)
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: S110 - headless box, no browser; URL is printed anyway
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nstopped.", err=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(app())

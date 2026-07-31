"""CLI entry point for prompt-coach."""

from __future__ import annotations

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
    return f"0 prompts · {label}", f"quiet week -- no prompts {label}. Try a wider --since."


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
    from prompt_coach.llm.client import LocalLLM, RemoteEndpointRefused

    try:
        llm = LocalLLM(
            cfg.llm.base_url,
            cfg.llm.model,
            api_key=cfg.llm.api_key,
            timeout=cfg.llm.timeout,
            allow_remote=cfg.llm.allow_remote,
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


def _setup_wizard():
    from prompt_coach.config import (
        MODEL_FIT_MODES,
        NUDGE_MODES,
        Config,
        LLMConfig,
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
            base_url, model, api_key=cfg.llm.api_key, timeout=2.0, allow_remote=cfg.llm.allow_remote
        )
        reachable = probe.available()
    except RemoteEndpointRefused as exc:
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
        llm=LLMConfig(
            base_url=base_url,
            model=model,
            api_key=cfg.llm.api_key,
            allow_remote=cfg.llm.allow_remote,
            timeout=cfg.llm.timeout,
        ),
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
        console.print(Text.assemble(("prompt-coach", "bold cyan"), (f" · {header}", "dim")))
        console.print(Text(body, style="dim"))
        return
    for store, reason in sync_stats.stores_failed.items():
        console.print(Text(f"⚠ {store}: sync failed ({reason})", style="yellow"))
    m = compute_metrics(prompts)
    # Rates are behavior, not quality -- no score-band colors here (D1).
    console.print(
        Text.assemble(
            ("prompt-coach stats", "bold cyan"), (f" · {label} · ", "dim"), ("no LLM", "dim")
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
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    console = Console(force_terminal=False, no_color=True) if plain else Console()
    label = _since_label(since, since_dt)
    if not prompts:
        header, body = _empty_state(cache, since_dt, label)
        console.print(Text.assemble(("prompt-coach", "bold cyan"), (f" · {header}", "dim")))
        console.print(Text(body, style="dim"))
        return
    for store, reason in stores_failed.items():
        console.print(Text(f"⚠ {store}: sync failed ({reason})", style="yellow"))
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
def nudge():
    """Hook target for Claude Code (UserPromptSubmit + Stop) and Hermes
    (pre_llm_call shell hook, tip-only -- see nudge.py's module docstring):
    prints a hook-response JSON to stdout, dispatching on the payload's
    hook_event_name. Mode ("coach"/"always"/"off") comes from config -- see
    nudge.py's module docstring. No store sync. In Claude Code's "coach"/
    "always" mode this may call the local LLM (bounded by its own short
    timeout, not the 120s default) and block the prompt; must never raise --
    a hook failure would block every prompt or response."""
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
def serve():
    """Read-only HTTP API (not yet available)."""
    typer.echo("serve is phase 2; not implemented yet.", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    sys.exit(app())

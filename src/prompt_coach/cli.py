"""CLI entry point for prompt-coach."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from prompt_coach.analysis.metrics import compute_metrics
from prompt_coach.analysis.patterns import detect_patterns
from prompt_coach.analysis.rubric import run_rubric
from prompt_coach.cache import CacheDB
from prompt_coach.config import Config, load_config
from prompt_coach.llm.client import LLMUnavailable, LocalLLM, RemoteEndpointRefused
from prompt_coach.models import ReportData
from prompt_coach.report.generator import build_report
from prompt_coach.stores.chatgpt_export import ChatGPTExportStore, looks_like_chatgpt_export
from prompt_coach.stores.claude_code import ClaudeCodeStore
from prompt_coach.stores.copilot import CopilotStore
from prompt_coach.stores.hermes import HermesStore
from prompt_coach.stores.json_import import JsonImportStore

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


def default_stores(cfg: Config) -> list:
    return [
        HermesStore(cfg.stores.hermes_db),
        ClaudeCodeStore(cfg.stores.claude_projects_dir),
        CopilotStore(cfg.stores.copilot_dir),
    ]


def open_cache(cfg: Config) -> CacheDB:
    return CacheDB(cfg.cache_dir / "cache.db")


def make_llm(cfg: Config) -> LocalLLM | None:
    """LocalLLM when the endpoint is reachable, else None (degraded mode)."""
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
def stats(
    since: str | None = typer.Option(None, "--since", help="Time range (e.g. 7d, 30d)"),
):
    """Quick overview of prompting metrics (deterministic, no LLM)."""
    cfg = load_config()
    cache = open_cache(cfg)
    cache.sync(default_stores(cfg))
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    if not prompts:
        typer.echo("No prompts in range. Run `prompt-coach cache sync` or widen --since.")
        raise typer.Exit(1)
    m = compute_metrics(prompts)
    typer.echo(f"{'Metric':32} {'Human':>10} {'Machine':>10}")
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
    for label, attr, fmt in rows:
        h = fmt.format(getattr(m.human, attr)) if m.human else "-"
        mc = fmt.format(getattr(m.machine, attr)) if m.machine else "-"
        typer.echo(f"{label:32} {h:>10} {mc:>10}")


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
    cfg = load_config()
    cache = open_cache(cfg)
    stats_ = cache.sync(default_stores(cfg), force=refresh)
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt, limit=limit)
    if not prompts:
        typer.echo("No prompts in range. Widen --since or check `prompt-coach discover`.")
        raise typer.Exit(1)

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
):
    """Terminal dashboard: volume trends, segments, rubric scores. No LLM, no content."""
    from rich.console import Console

    from prompt_coach.report.dash import build_dash, weekly_volumes

    cfg = load_config()
    cache = open_cache(cfg)
    cache.sync(default_stores(cfg))
    since_dt = parse_since(since)
    prompts = cache.prompts(since=since_dt)
    if not prompts:
        typer.echo("No prompts in range. Widen --since or check `prompt-coach discover`.")
        raise typer.Exit(1)
    renderable = build_dash(
        metrics=compute_metrics(prompts),
        rubric=run_rubric(prompts, None, cache),
        volumes=weekly_volumes(prompts),
        prompt_count=len(prompts),
        session_count=len({f"{p.source}:{p.session_id}" for p in prompts}),
        since_label=f"since {since_dt.date().isoformat()}" if since_dt else "all time",
    )
    console = Console(force_terminal=False, no_color=True) if plain else Console()
    console.print(renderable)


@app.command()
def query(
    question: str = typer.Argument(help="Question about your prompt history"),
):
    """Ask a natural-language question about your prompt history."""
    from prompt_coach.query import answer

    cfg = load_config()
    cache = open_cache(cfg)
    cache.sync(default_stores(cfg))
    typer.echo(answer(question, cache, make_llm(cfg)))


@app.command("import")
def import_(
    file: Path = typer.Argument(help="Path to a JSON/ZIP export (ChatGPT or simple format)"),
):
    """Import external session data into the cache (format auto-detected)."""
    import json as _json

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
    s = open_cache(cfg).sync(default_stores(cfg), force=refresh)
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
def serve():
    """Read-only HTTP API (not yet available)."""
    typer.echo("serve is phase 2; not implemented yet.", err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
    sys.exit(app())

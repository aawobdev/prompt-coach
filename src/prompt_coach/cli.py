"""CLI entry point for prompt-coach."""

from typing import Optional

import typer

app = typer.Typer(
    name="prompt-coach",
    help="Your personal prompting analyst. Runs locally. Zero data export.",
    no_args_is_help=True,
)


@app.command()
def discover(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Hermes profile name"),
):
    """Find all available session stores on this machine."""
    typer.echo("🔍 Discovering session stores...")
    # TODO: implement store discovery
    typer.echo("  No stores found. Use --import to load external data.")


@app.command()
def report(
    store: int = typer.Option(1, "--store", "-s", help="Store index from `discover`"),
    since: Optional[str] = typer.Option(None, "--since", help="Time range (e.g. 7d, 30d)"),
    limit: int = typer.Option(50, "--limit", "-l", help="Max sessions to analyse"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Local LLM model name"),
):
    """Generate a coaching report from your prompt history."""
    typer.echo("📊 Generating coaching report...")
    # TODO: implement report generation


@app.command()
def stats(
    store: int = typer.Option(1, "--store", "-s", help="Store index from `discover`"),
):
    """Quick overview of prompting metrics."""
    typer.echo("📈 Computing stats...")
    # TODO: implement stats


@app.command()
def query(
    question: str = typer.Argument(help="Question about your prompt history"),
):
    """Ask a natural-language question about your prompt history."""
    typer.echo(f"❓ {question}")
    # TODO: implement query


@app.command()
def import_(
    file: str = typer.Argument(help="Path to JSON file with session data"),
):
    """Import external session data (JSON format)."""
    typer.echo(f"📥 Importing sessions from {file}...")
    # TODO: implement import


@app.command()
def serve(
    port: int = typer.Option(9090, "--port", "-p", help="HTTP port"),
):
    """Start a read-only HTTP API."""
    typer.echo(f"🌐 Starting API on :{port}...")
    # TODO: implement server

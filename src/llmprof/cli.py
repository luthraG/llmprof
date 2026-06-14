"""llmprof command line: `llmprof up` and `llmprof traces`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .store import Store

app = typer.Typer(add_completion=False, help="pprof for your LLM context.")
console = Console()


@app.command()
def up(
    host: str = typer.Option("127.0.0.1", help="Host to bind."),
    port: int = typer.Option(4000, help="Port to bind."),
    upstream: str = typer.Option(
        None, help="Upstream API base URL (default: OpenAI, or $LLMPROF_UPSTREAM)."
    ),
):
    """Start the profiling proxy."""
    import uvicorn

    from .proxy import create_app

    application = create_app(upstream=upstream)
    base = f"http://{host}:{port}/v1"
    console.print(
        Panel.fit(
            f"[bold green]llmprof[/] is profiling on [cyan]{base}[/]\n\n"
            f"Point your client at it:\n"
            f"  [dim]OpenAI:[/]  base_url = [cyan]{base}[/]\n"
            f"  upstream = [magenta]{application.state.upstream}[/]\n\n"
            f"Then run [bold]llmprof traces[/] to see where your tokens went.",
            title="llmprof",
            border_style="green",
        )
    )
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def traces(limit: int = typer.Option(20, help="How many recent calls to show.")):
    """Show recent captured calls with token + cost breakdown."""
    rows = Store().recent(limit)
    if not rows:
        console.print(
            "[yellow]No traces yet.[/] Start the proxy with "
            "[bold]llmprof up[/] and send a request."
        )
        raise typer.Exit()

    table = Table(title=f"last {len(rows)} calls", header_style="bold green")
    table.add_column("model")
    table.add_column("prompt", justify="right")
    table.add_column("completion", justify="right")
    table.add_column("total", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("top component")

    for r in rows:
        comps = r.get("components") or {}
        top = max(comps.items(), key=lambda kv: kv[1]) if comps else ("-", 0)
        cost = r.get("cost_usd")
        cost_s = f"${cost:.4f}" if cost is not None else "[dim]?[/]"
        table.add_row(
            r.get("model") or "-",
            str(r.get("prompt_tokens") or 0),
            str(r.get("completion_tokens") or 0),
            str(r.get("total_tokens") or 0),
            cost_s,
            f"{top[0]} ({top[1]})",
        )
    console.print(table)


@app.command()
def version():
    """Print version."""
    console.print(f"llmprof {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

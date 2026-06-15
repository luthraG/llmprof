"""llmprof command line: `llmprof up` and `llmprof traces`."""

from __future__ import annotations

import socket

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .store import open_store

app = typer.Typer(add_completion=False, help="pprof for your LLM context.")
console = Console()


def _port_available(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


@app.command()
def up(
    host: str = typer.Option("127.0.0.1", "--host", envvar="LLMPROF_HOST", help="Host to bind."),
    port: int = typer.Option(4000, "--port", envvar="LLMPROF_PORT", help="Port to bind."),
    upstream: str = typer.Option(
        None, "--upstream", envvar="LLMPROF_UPSTREAM",
        help="OpenAI-compatible upstream base URL (default: OpenAI). Point this "
        "at any OpenAI-compatible provider (Groq, Together, ...).",
    ),
    anthropic_upstream: str = typer.Option(
        None, "--anthropic-upstream", envvar="LLMPROF_ANTHROPIC_UPSTREAM",
        help="Anthropic upstream base URL (default: Anthropic).",
    ),
):
    """Start the profiling proxy. One instance routes OpenAI and Anthropic
    clients to their own upstreams, so it profiles both at once."""
    if not _port_available(host, port):
        console.print(
            f"[red]Port {port} on {host} is already in use.[/]\n"
            f"Pick another, e.g. [bold]llmprof up --port {port + 1}[/], "
            f"or set [bold]LLMPROF_PORT[/]."
        )
        raise typer.Exit(code=1)

    import uvicorn

    from .proxy import create_app

    application = create_app(upstream=upstream, anthropic_upstream=anthropic_upstream)
    ups = application.state.upstreams
    console.print(
        Panel.fit(
            f"[bold green]llmprof[/] is profiling on [cyan]http://{host}:{port}[/]\n\n"
            f"Point your clients at it (key passes through):\n"
            f"  [dim]OpenAI:[/]    base_url = [cyan]http://{host}:{port}/v1[/]  "
            f"[dim]->[/] [magenta]{ups['openai']}[/]\n"
            f"  [dim]Anthropic:[/] base_url = [cyan]http://{host}:{port}[/]     "
            f"[dim]->[/] [magenta]{ups['anthropic']}[/]\n\n"
            f"Open [cyan]http://{host}:{port}[/] for the dashboard, or "
            f"[bold]llmprof traces[/].",
            title="llmprof",
            border_style="green",
        )
    )
    uvicorn.run(application, host=host, port=port, log_level="warning")


@app.command()
def traces(limit: int = typer.Option(20, help="How many recent calls to show.")):
    """Show recent captured calls with token + cost breakdown."""
    rows = open_store().recent(limit)
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
def reset(yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation.")):
    """Delete all captured traces (clears the dashboard for a clean slate)."""
    if not yes:
        typer.confirm("Delete all captured traces?", abort=True)
    deleted = open_store().clear()
    console.print(f"[green]Cleared {deleted} traces.[/]")


@app.command()
def selftest(corpus: str = typer.Option(
        None, help="Also replay every *.json fixture in this directory "
        "(e.g. one recorded with LLMPROF_CAPTURE).")):
    """Replay request/response fixtures through the real pipeline and check the
    recorded trace: token capture, cost, and invariants like cached <= prompt and
    reclaimable <= spend. Catches data-correctness regressions without a live API."""
    from .selftest import run

    ok, results = run(corpus)
    for name, problems in results:
        if problems:
            console.print(f"[red]FAIL[/] {name}")
            for p in problems:
                console.print(f"   [red]- {p}[/]")
        else:
            console.print(f"[green]PASS[/] {name}")
    passed = sum(1 for _, p in results if not p)
    if not ok:
        console.print(f"[red]{passed}/{len(results)} replay checks passed[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]all {len(results)} replay checks passed[/]")


@app.command()
def version():
    """Print version."""
    console.print(f"llmprof {__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()

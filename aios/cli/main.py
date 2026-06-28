from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..runtime.process import ProcessManager as PM

app = typer.Typer(
    name="aios",
    help="[bold]Ai.os[/bold] — persistent agent runtime",
    add_completion=True,
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()


def _dot(running: bool) -> Text:
    return Text("● ", style="green bold") if running else Text("● ", style="red dim")


# ── run ───────────────────────────────────────────────────────────────────────

@app.command()
def run(
    agent_file: Path = typer.Argument(..., help="Path to the agent Python file"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Restart on file change"),
    detach: bool = typer.Option(False, "--detach", "-d", help="Run in background"),
) -> None:
    """Start an agent."""
    if not agent_file.exists():
        console.print(f"[red]File not found:[/red] {agent_file}")
        raise typer.Exit(1)

    agent_name = agent_file.stem

    if detach:
        pid = PM.spawn(agent_file.resolve(), agent_name)
        console.print(
            Panel(
                f"[green]Agent started[/green]\n\n"
                f"  name  [dim]→[/dim]  [bold]{agent_name}[/bold]\n"
                f"  pid   [dim]→[/dim]  {pid}\n"
                f"  logs  [dim]→[/dim]  [dim]aios logs {agent_name} -f[/dim]",
                title="[bold]Ai.os[/bold]",
                border_style="dim",
                padding=(0, 1),
            )
        )
        return

    if watch:
        try:
            from watchfiles import run_process
        except ImportError:
            console.print("[red]watchfiles not installed:[/red] pip install watchfiles")
            raise typer.Exit(1)
        console.print(f"[dim]Watching {agent_file} — restarts on change (Ctrl+C to stop)[/dim]")
        run_process(str(agent_file.parent), target=_run_blocking, args=(agent_file,))
    else:
        _run_blocking(agent_file)


def _run_blocking(agent_file: Path) -> None:
    subprocess.run([sys.executable, str(agent_file)], check=False)


# ── list ──────────────────────────────────────────────────────────────────────

@app.command(name="list")
def list_agents() -> None:
    """List all known agents and their status."""
    agents = PM.list_agents()

    if not agents:
        console.print(
            "[dim]No agents found.[/dim]\n"
            "  Start one: [bold]aios run myagent.py --detach[/bold]"
        )
        return

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0, 1))
    table.add_column("", width=2, no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("PID", justify="right", style="dim")
    table.add_column("Status")
    table.add_column("File", style="dim")

    for a in sorted(agents, key=lambda x: x["name"]):
        running = a.get("running", False)
        table.add_row(
            _dot(running),
            a["name"],
            str(a.get("pid", "—")),
            "[green]running[/green]" if running else "[dim]stopped[/dim]",
            str(Path(a.get("file", "—")).name),
        )

    console.print(table)


# ── status ────────────────────────────────────────────────────────────────────

@app.command()
def status(agent_name: str = typer.Argument(..., help="Agent name")) -> None:
    """Show agent status, identity, and recent runs."""
    running = PM.is_running(agent_name)
    info = PM.get_info(agent_name)

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    run_history: list[dict] = []

    if db_path.exists():
        async def _fetch() -> list[dict]:
            from ..runtime.checkpoint import CheckpointEngine
            engine = CheckpointEngine(agent_id="", db_path=db_path)
            # Read directly without needing agent_id
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                try:
                    rows = await (await db.execute(
                        "SELECT id, status, started_at, ended_at FROM agent_runs ORDER BY started_at DESC LIMIT 5"
                    )).fetchall()
                    return [{"id": r[0][:8], "status": r[1], "started": r[2][:19], "ended": (r[3] or "")[:19]} for r in rows]
                except Exception:
                    return []
        run_history = asyncio.run(_fetch())

    dot = _dot(running)
    state = "[green]running[/green]" if running else "[dim]stopped[/dim]"

    lines = [f"{dot}{state}"]
    if info:
        lines += [
            f"  pid   [dim]→[/dim]  {info.get('pid', '—')}",
            f"  file  [dim]→[/dim]  [dim]{info.get('file', '—')}[/dim]",
        ]

    if run_history:
        lines.append("")
        lines.append("[dim]recent runs[/dim]")
        for r in run_history:
            icon = "✓" if r["status"] == "completed" else ("●" if r["status"] == "running" else "✗")
            color = "green" if r["status"] == "completed" else ("yellow" if r["status"] == "running" else "red")
            lines.append(f"  [{color}]{icon}[/{color}]  [dim]{r['id']}[/dim]  {r['started']}")

    console.print(Panel("\n".join(lines), title=f"[bold]{agent_name}[/bold]", border_style="dim", padding=(0, 1)))


# ── logs ──────────────────────────────────────────────────────────────────────

@app.command()
def logs(
    agent_name: str = typer.Argument(..., help="Agent name"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs live"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Show or follow agent logs."""
    log_path = PM.log_file(agent_name)

    if not log_path.exists():
        console.print(f"[dim]No logs yet for [bold]{agent_name}[/bold][/dim]")
        return

    if follow:
        console.print(f"[dim]Following {log_path} — Ctrl+C to stop[/dim]\n")
        try:
            _tail_follow(log_path, lines)
        except KeyboardInterrupt:
            pass
    else:
        content = log_path.read_text(errors="replace")
        tail_lines = content.splitlines()[-lines:]
        console.print("\n".join(tail_lines))


def _tail_follow(path: Path, initial_lines: int) -> None:
    """Cross-platform log follower — works on Windows and Unix."""
    with path.open("r", errors="replace") as f:
        # Print existing tail
        all_lines = f.readlines()
        for line in all_lines[-initial_lines:]:
            print(line, end="")

        # Follow new lines
        while True:
            line = f.readline()
            if line:
                print(line, end="", flush=True)
            else:
                time.sleep(0.1)


# ── stop ──────────────────────────────────────────────────────────────────────

@app.command()
def stop(agent_name: str = typer.Argument(..., help="Agent name")) -> None:
    """Stop a running agent."""
    if PM.stop(agent_name):
        console.print(f"[green]Stopped[/green] [bold]{agent_name}[/bold]")
    else:
        console.print(f"[dim]Agent [bold]{agent_name}[/bold] is not running[/dim]")


# ── restart ───────────────────────────────────────────────────────────────────

@app.command()
def restart(agent_name: str = typer.Argument(..., help="Agent name")) -> None:
    """Stop and restart an agent (resumes from last checkpoint)."""
    info = PM.get_info(agent_name)
    was_running = PM.is_running(agent_name)
    PM.stop(agent_name)

    if was_running:
        time.sleep(0.4)

    if info and info.get("file"):
        pid = PM.spawn(Path(info["file"]), agent_name)
        console.print(f"[green]Restarted[/green] [bold]{agent_name}[/bold]  [dim](pid {pid})[/dim]")
        console.print(f"  [dim]Agent will resume from last checkpoint[/dim]")
    else:
        console.print(f"[red]Cannot restart[/red] [bold]{agent_name}[/bold] — no file info found")
        console.print(f"  Run it manually: [bold]aios run <file> --detach[/bold]")


# ── memory ────────────────────────────────────────────────────────────────────

@app.command()
def memory(
    agent_name: str = typer.Argument(..., help="Agent name"),
    key: str = typer.Option("", "--key", "-k", help="Show only this key"),
) -> None:
    """Inspect an agent's long-term memory."""
    import json

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    if not db_path.exists():
        console.print(f"[dim]No data found for [bold]{agent_name}[/bold][/dim]")
        return

    async def _fetch() -> list[tuple[str, str, str]]:
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            try:
                if key:
                    rows = await (await db.execute(
                        "SELECT key, value, updated_at FROM memory_long WHERE key = ? ORDER BY updated_at DESC",
                        (key,),
                    )).fetchall()
                else:
                    rows = await (await db.execute(
                        "SELECT key, value, updated_at FROM memory_long ORDER BY updated_at DESC"
                    )).fetchall()
                return [(r[0], r[1], r[2]) for r in rows]
            except Exception:
                return []

    rows = asyncio.run(_fetch())

    if not rows:
        msg = f"Key [bold]{key}[/bold] not found" if key else "Memory is empty"
        console.print(f"[dim]{msg} for [bold]{agent_name}[/bold][/dim]")
        return

    if key and len(rows) == 1:
        # Full value for a single key
        try:
            val = json.loads(rows[0][1])
            console.print_json(json.dumps(val))
        except Exception:
            console.print(rows[0][1])
        return

    table = Table(box=box.SIMPLE, header_style="bold dim", padding=(0, 1))
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Preview")
    table.add_column("Updated", style="dim", no_wrap=True)

    for k, raw, updated_at in rows:
        try:
            val = json.loads(raw)
            preview = str(val)
        except Exception:
            preview = raw
        table.add_row(k, preview[:90] + ("…" if len(preview) > 90 else ""), updated_at[:19])

    console.print(table)


# ── doctor ───────────────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """Check your Ai.os environment for common issues."""
    import sys
    import os
    import importlib

    console.print(f"\n[bold]Ai.os Doctor[/bold]  [dim]v{_get_version()}[/dim]\n")

    ok_count = 0
    warn_count = 0
    fail_count = 0

    def check(label: str, passed: bool, detail: str = "", warn: bool = False) -> None:
        nonlocal ok_count, warn_count, fail_count
        if passed:
            icon, color = "✓", "green"
            ok_count += 1
        elif warn:
            icon, color = "▲", "yellow"
            warn_count += 1
        else:
            icon, color = "✗", "red"
            fail_count += 1
        line = f"  [{color}]{icon}[/{color}]  {label}"
        if detail:
            line += f"  [dim]{detail}[/dim]"
        console.print(line)

    # Python version
    v = sys.version_info
    check(
        "Python version",
        v >= (3, 10),
        f"{v.major}.{v.minor}.{v.micro}",
        warn=False,
    )

    # Required packages
    packages = ["aiosqlite", "litellm", "typer", "rich", "fastapi", "uvicorn", "httpx", "pydantic"]
    for pkg in packages:
        try:
            importlib.import_module(pkg.replace("-", "_"))
            check(f"Package: {pkg}", True)
        except ImportError:
            check(f"Package: {pkg}", False, "pip install " + pkg)

    # Optional packages
    for pkg in ["watchfiles"]:
        try:
            importlib.import_module(pkg.replace("-", "_"))
            check(f"Package: {pkg} (optional)", True)
        except ImportError:
            check(f"Package: {pkg} (optional)", False, "pip install " + pkg, warn=True)

    # Data directory
    data_dir = PM.AIOS_DIR
    check(
        "Data directory",
        True,
        str(data_dir),
    )

    # API keys
    console.print()
    keys = [
        ("ANTHROPIC_API_KEY", "claude-* models"),
        ("OPENAI_API_KEY", "gpt-* models"),
        ("GOOGLE_API_KEY", "gemini-* models"),
        ("MISTRAL_API_KEY", "mistral-* models"),
    ]
    any_key = False
    for env_var, label in keys:
        val = os.environ.get(env_var, "")
        has = bool(val)
        if has:
            any_key = True
        check(
            f"{env_var}",
            has,
            label + (" — set" if has else " — missing"),
            warn=not has,
        )

    if not any_key:
        console.print(
            "\n  [yellow]No API keys found.[/yellow] Add at least one to .env:\n"
            "    ANTHROPIC_API_KEY=your-key-here\n"
        )

    # Dot env file
    console.print()
    env_file = Path(".env")
    check(".env file", env_file.exists(), str(env_file.resolve()), warn=not env_file.exists())

    # Summary
    console.print()
    if fail_count == 0 and warn_count == 0:
        console.print("  [green bold]All checks passed.[/green bold] Ready to run agents.\n")
    elif fail_count == 0:
        console.print(f"  [yellow]{warn_count} warning(s)[/yellow] — optional items missing.\n")
    else:
        console.print(
            f"  [red]{fail_count} error(s)[/red], {warn_count} warning(s) — fix errors before running agents.\n"
        )


def _get_version() -> str:
    try:
        from .. import __version__
        return __version__
    except Exception:
        return "unknown"


# ── ui ────────────────────────────────────────────────────────────────────────

@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Host to bind"),
    port: int = typer.Option(7851, help="Port to bind"),
) -> None:
    """Open the web UI dashboard."""
    import webbrowser
    import threading

    def _open() -> None:
        time.sleep(1.2)
        webbrowser.open(f"http://{host}:{port}")

    threading.Thread(target=_open, daemon=True).start()
    console.print(f"[bold]Ai.os UI[/bold] → [link=http://{host}:{port}]http://{host}:{port}[/link]")

    import uvicorn
    from ..web.app import create_app
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


# ── init ─────────────────────────────────────────────────────────────────────

@app.command()
def init(
    agent_name: str = typer.Argument("myagent", help="Name for the new agent"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", help="Default LLM model"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Target directory"),
) -> None:
    """Scaffold a new agent project."""
    from .templates import AGENT_TEMPLATE, ENV_TEMPLATE, GITIGNORE_TEMPLATE

    target = directory / agent_name
    target.mkdir(parents=True, exist_ok=True)

    class_name = "".join(word.capitalize() for word in agent_name.split("_")) + "Agent"

    agent_file = target / f"{agent_name}.py"
    env_file = target / ".env"
    gitignore = target / ".gitignore"

    files_written = []

    if not agent_file.exists():
        agent_file.write_text(
            AGENT_TEMPLATE.format(class_name=class_name, agent_name=agent_name, model=model),
            encoding="utf-8",
        )
        files_written.append(str(agent_file))

    if not env_file.exists():
        env_file.write_text(
            ENV_TEMPLATE.format(agent_name=agent_name),
            encoding="utf-8",
        )
        files_written.append(str(env_file))

    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
        files_written.append(str(gitignore))

    console.print(
        Panel(
            f"[green]Agent scaffolded[/green]\n\n"
            + "\n".join(f"  [dim]created[/dim]  {f}" for f in files_written)
            + f"\n\n[dim]Next steps:[/dim]\n"
            f"  1. Add your API key to [bold]{target}/.env[/bold]\n"
            f"  2. Edit [bold]{agent_file.name}[/bold] — implement run() and add @tool methods\n"
            f"  3. Run it: [bold]aios run {agent_file}[/bold]",
            title="[bold]Ai.os init[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


# ── version ───────────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Show Ai.os version."""
    from .. import __version__
    console.print(f"[bold]Ai.os[/bold]  [dim]v{__version__}[/dim]")


if __name__ == "__main__":
    app()

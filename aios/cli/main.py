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
                f"[green]Agent started[/green]\n\n  name  [dim]→[/dim]  [bold]{agent_name}[/bold]\n  pid   [dim]→[/dim]  {pid}\n  logs  [dim]→[/dim]  [dim]aios logs {agent_name} -f[/dim]",
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


def _stream_remote_logs(agent_name: str, host: str) -> None:
    """Connect to a remote Ai.os web UI and stream logs via WebSocket."""
    host = host.rstrip("/")
    if host.startswith("http://"):
        ws_url = "ws://" + host[7:]
    elif host.startswith("https://"):
        ws_url = "wss://" + host[8:]
    else:
        ws_url = host
    ws_url = f"{ws_url}/ws/agents/{agent_name}/logs"

    try:
        import websockets
    except ImportError:
        console.print("[red]websockets not installed:[/red] pip install websockets")
        raise typer.Exit(1)

    console.print(f"[dim]Connecting to[/dim] [bold]{ws_url}[/bold] [dim](Ctrl+C to stop)[/dim]")

    async def _recv():
        async with websockets.connect(ws_url) as ws:
            async for msg in ws:
                console.print(msg)

    try:
        asyncio.run(_recv())
    except KeyboardInterrupt:
        console.print("\n[dim]Disconnected.[/dim]")


# ── list ──────────────────────────────────────────────────────────────────────


@app.command(name="list")
def list_agents() -> None:
    """List all known agents and their status."""
    agents = PM.list_agents()

    if not agents:
        console.print("[dim]No agents found.[/dim]\n  Start one: [bold]aios run myagent.py --detach[/bold]")
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
            # from ..runtime.checkpoint import CheckpointEngine

            # engine = CheckpointEngine(agent_id="", db_path=db_path)
            # Read directly without needing agent_id
            import aiosqlite

            async with aiosqlite.connect(db_path) as db:
                try:
                    rows = await (await db.execute("SELECT id, status, started_at, ended_at FROM agent_runs ORDER BY started_at DESC LIMIT 5")).fetchall()
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


# ── runs ──────────────────────────────────────────────────────────────────────


@app.command()
def runs(
    agent_name: str = typer.Argument(..., help="Agent name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum rows to show"),
    failed: bool = typer.Option(False, "--failed", "-f", help="Show only failed runs"),
) -> None:
    """Show run history for an agent with duration, tokens, and errors.

    \\b
    Examples:
      aios runs myagent
      aios runs myagent --failed
      aios runs myagent --limit 50
    """
    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    if not db_path.exists():
        console.print(f"[dim]No data for [bold]{agent_name}[/bold][/dim]")
        raise typer.Exit(1)

    async def _fetch():
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            try:
                where = "WHERE status = 'failed'" if failed else ""
                rows = await (await db.execute(
                    f"SELECT id, status, started_at, ended_at, total_tokens, llm_calls, error "
                    f"FROM agent_runs {where} ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                )).fetchall()
                return rows
            except Exception:
                return []

    rows = asyncio.run(_fetch())

    if not rows:
        msg = "No failed runs" if failed else "No runs yet"
        console.print(f"[dim]{msg} for [bold]{agent_name}[/bold][/dim]")
        return

    table = Table(box=box.SIMPLE, header_style="bold dim", padding=(0, 1))
    table.add_column("Run ID", style="dim", no_wrap=True, width=10)
    table.add_column("Status", no_wrap=True)
    table.add_column("Started", style="dim", no_wrap=True)
    table.add_column("Duration", no_wrap=True)
    table.add_column("Tokens", justify="right")
    table.add_column("LLM calls", justify="right")

    for row in rows:
        run_id, status, started_at, ended_at, total_tokens, llm_calls, error = row

        if status == "completed":
            status_cell = "[green]✓ done[/green]"
        elif status == "running":
            status_cell = "[yellow]● running[/yellow]"
        else:
            status_cell = "[red]✗ failed[/red]"

        # Duration
        dur_cell = "—"
        if started_at and ended_at:
            try:
                from datetime import datetime
                fmt = "%Y-%m-%d %H:%M:%S"
                s = datetime.strptime(started_at[:19], fmt)
                e = datetime.strptime(ended_at[:19], fmt)
                secs = int((e - s).total_seconds())
                dur_cell = _fmt_dur(secs)
            except Exception:
                pass

        tok_cell = _fmt_tokens(total_tokens or 0)
        llm_cell = str(llm_calls or 0)

        table.add_row(
            (run_id or "")[:8],
            status_cell,
            (started_at or "")[:19],
            dur_cell,
            tok_cell,
            llm_cell,
        )

    console.print(table)

    # Show error message for last failed run if present
    if rows:
        last_error = rows[0][6]
        if last_error and not failed:
            pass  # only show inline in --failed mode
        if last_error and failed:
            console.print(f"\n[dim]Last error:[/dim]\n[red]{last_error[:300]}[/red]")


# ── timeline ─────────────────────────────────────────────────────────────────


@app.command()
def timeline(
    agent_name: str = typer.Argument(..., help="Agent name"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum events to show"),
    event_type: str = typer.Option("", "--type", "-t", help="Filter by event type"),
) -> None:
    """Show an agent's append-only event timeline.

    \\b
    Examples:
      aios timeline myagent
      aios timeline myagent --type run_complete
      aios timeline myagent --limit 100
    """
    import json

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    if not db_path.exists():
        console.print(f"[dim]No data for [bold]{agent_name}[/bold][/dim]")
        raise typer.Exit(1)

    async def _fetch():
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            try:
                if event_type:
                    rows = await (await db.execute(
                        "SELECT event_type, data, created_at FROM memory_timeline "
                        "WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                        (event_type, limit),
                    )).fetchall()
                else:
                    rows = await (await db.execute(
                        "SELECT event_type, data, created_at FROM memory_timeline "
                        "ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )).fetchall()
                return rows
            except Exception:
                return []

    rows = asyncio.run(_fetch())

    if not rows:
        msg = f"No events of type '{event_type}'" if event_type else "No timeline events"
        console.print(f"[dim]{msg} for [bold]{agent_name}[/bold][/dim]")
        return

    table = Table(box=box.SIMPLE, header_style="bold dim", padding=(0, 1))
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Event type", style="bold", no_wrap=True)
    table.add_column("Data")

    for event_t, data_raw, created_at in rows:
        try:
            data_obj = json.loads(data_raw) if data_raw else {}
            data_preview = str(data_obj)[:80] + ("…" if len(str(data_obj)) > 80 else "")
        except Exception:
            data_preview = (data_raw or "")[:80]

        table.add_row((created_at or "")[:19], event_t or "—", data_preview)

    console.print(table)


# ── logs ──────────────────────────────────────────────────────────────────────


@app.command()
def logs(
    agent_name: str = typer.Argument(..., help="Agent name"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs live"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    remote: str = typer.Option("", "--remote", "-r", help="Remote host URL (e.g. ws://myserver:8000) to stream logs from a cloud-hosted agent"),
) -> None:
    """Show or follow agent logs (local or remote).

    \\b
    Examples:
      aios logs myagent -f
      aios logs myagent --remote ws://my-server:8000
    """
    if remote:
        _stream_remote_logs(agent_name, remote)
        return
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
        console.print("  [dim]Agent will resume from last checkpoint[/dim]")
    else:
        console.print(f"[red]Cannot restart[/red] [bold]{agent_name}[/bold] — no file info found")
        console.print("  Run it manually: [bold]aios run <file> --detach[/bold]")


# ── cp ───────────────────────────────────────────────────────────────────────


@app.command()
def cp(
    source: str = typer.Argument(..., help="Source agent name"),
    dest: str = typer.Argument(..., help="Destination agent name"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Skip copying long-term memory"),
    no_timeline: bool = typer.Option(False, "--no-timeline", help="Skip copying timeline events"),
) -> None:
    """Clone an agent — copy its memory and timeline to a new name.

    The destination agent will have a fresh identity (new UUID) but
    inherit the source agent's long-term memory and event timeline.

    \\b
    Examples:
      aios cp researcher researcher-v2
      aios cp researcher researcher-v2 --no-timeline
    """
    import shutil

    src_db = PM.AIOS_DIR / "data" / f"{source}.db"
    dst_db = PM.AIOS_DIR / "data" / f"{dest}.db"

    if not src_db.exists():
        console.print(f"[red]Source agent not found:[/red] {source}")
        raise typer.Exit(1)

    if dst_db.exists():
        console.print(f"[red]Destination already exists:[/red] {dest}  (use [bold]aios memory {dest} --delete[/bold] or choose a different name)")
        raise typer.Exit(1)

    # Start from a copy, then strip what the user doesn't want + reset identity
    shutil.copy2(src_db, dst_db)

    async def _fixup() -> None:
        import aiosqlite
        async with aiosqlite.connect(dst_db) as db:
            # Give the clone a new identity UUID
            import uuid
            new_id = str(uuid.uuid4())
            try:
                await db.execute("UPDATE identity SET id = ?, name = ? WHERE 1", (new_id, dest))
            except Exception:
                pass

            if no_memory:
                try:
                    await db.execute("DELETE FROM memory_long")
                except Exception:
                    pass

            if no_timeline:
                try:
                    await db.execute("DELETE FROM memory_timeline")
                except Exception:
                    pass

            # Clear run history — it belongs to the source
            try:
                await db.execute("DELETE FROM agent_runs")
            except Exception:
                pass
            try:
                await db.execute("DELETE FROM tool_checkpoints")
            except Exception:
                pass

            await db.commit()

    asyncio.run(_fixup())

    parts = ["memory"]
    if not no_timeline:
        parts.append("timeline")
    console.print(f"[green]✓[/green] Cloned [bold]{source}[/bold] → [bold]{dest}[/bold]  [dim]({', '.join(parts)} copied; fresh run history)[/dim]")


# ── memory ────────────────────────────────────────────────────────────────────


@app.command()
def memory(
    agent_name: str = typer.Argument(..., help="Agent name"),
    key: str = typer.Option("", "--key", "-k", help="Show only this key"),
    set_value: str = typer.Option("", "--set", "-s", help="Set key=value (JSON or plain string). Use with --key."),
    delete: bool = typer.Option(False, "--delete", "-d", help="Delete the key specified by --key"),
) -> None:
    """Inspect, set, or delete an agent's long-term memory.

    \\b
    Examples:
      aios memory myagent                         # list all keys
      aios memory myagent --key last_result       # show full value
      aios memory myagent --key greeting --set "Hello world"
      aios memory myagent --key old_key --delete
    """
    import json

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"

    # ── Write / delete operations ─────────────────────────────────────────────
    if set_value and key:
        if not db_path.exists():
            console.print(f"[red]No data found for[/red] [bold]{agent_name}[/bold] (agent has never run)")
            raise typer.Exit(1)

        try:
            raw = json.dumps(json.loads(set_value))  # validate + normalise JSON
        except json.JSONDecodeError:
            raw = json.dumps(set_value)  # treat as plain string

        async def _set() -> None:
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "INSERT INTO memory_long (key, value, updated_at) VALUES (?, ?, datetime('now'))"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, raw),
                )
                await db.commit()

        asyncio.run(_set())
        console.print(f"[green]✓[/green] Set [bold]{key}[/bold] = {raw[:80]}")
        return

    if delete and key:
        if not db_path.exists():
            console.print(f"[dim]No data for [bold]{agent_name}[/bold][/dim]")
            return

        async def _del() -> int:
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                cur = await db.execute("DELETE FROM memory_long WHERE key = ?", (key,))
                await db.commit()
                return cur.rowcount

        n = asyncio.run(_del())
        if n:
            console.print(f"[green]✓[/green] Deleted [bold]{key}[/bold]")
        else:
            console.print(f"[dim]Key [bold]{key}[/bold] not found[/dim]")
        return

    if not db_path.exists():
        console.print(f"[dim]No data found for [bold]{agent_name}[/bold][/dim]")
        return

    async def _fetch() -> list[tuple[str, str, str]]:
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            try:
                if key:
                    rows = await (
                        await db.execute(
                            "SELECT key, value, updated_at FROM memory_long WHERE key = ? ORDER BY updated_at DESC",
                            (key,),
                        )
                    ).fetchall()
                else:
                    rows = await (await db.execute("SELECT key, value, updated_at FROM memory_long ORDER BY updated_at DESC")).fetchall()
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
    import importlib
    import os
    import sys

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
    optional_pkgs = [
        ("watchfiles", "aios run --watch"),
        ("asyncpg", "PostgresMixin  · pip install \"aios-runtime[postgres]\""),
        ("uvicorn", "aios mcp (HTTP/SSE transport)"),
        ("websockets", "aios logs --remote"),
    ]
    for pkg, hint in optional_pkgs:
        try:
            importlib.import_module(pkg.replace("-", "_"))
            check(f"Package: {pkg} (optional)", True)
        except ImportError:
            check(f"Package: {pkg} (optional)", False, hint, warn=True)

    # Data directory
    data_dir = PM.AIOS_DIR
    check("Data directory", True, str(data_dir))

    # LLM API keys
    console.print()
    console.print("  [dim]LLM providers[/dim]")
    llm_keys = [
        ("ANTHROPIC_API_KEY", "claude-* models"),
        ("OPENAI_API_KEY", "gpt-* models"),
        ("GOOGLE_API_KEY", "gemini-* models"),
        ("MISTRAL_API_KEY", "mistral-* models"),
    ]
    any_key = False
    for env_var, label in llm_keys:
        val = os.environ.get(env_var, "")
        has = bool(val)
        if has:
            any_key = True
        check(env_var, has, label + (" — set" if has else " — missing"), warn=not has)

    if not any_key:
        console.print("\n  [yellow]No LLM API keys found.[/yellow] Add at least one to .env:\n    ANTHROPIC_API_KEY=your-key-here\n")

    # Tool mixin credentials (all optional)
    console.print()
    console.print("  [dim]Tool mixin credentials (optional)[/dim]")
    mixin_keys = [
        ("GITHUB_TOKEN", "GitHubMixin"),
        ("SLACK_BOT_TOKEN", "SlackMixin"),
        ("DISCORD_WEBHOOK_URL", "DiscordMixin (webhook)"),
        ("NOTION_TOKEN", "NotionMixin"),
        ("LINEAR_API_KEY", "LinearMixin"),
        ("POSTGRES_URL", "PostgresMixin"),
        ("EMAIL_ADDRESS", "EmailMixin"),
    ]
    for env_var, label in mixin_keys:
        val = os.environ.get(env_var, "")
        check(env_var, bool(val), label + (" — set" if val else " — not configured"), warn=not val)

    # Dot env file
    console.print()
    env_file = Path(".env")
    check(".env file", env_file.exists(), str(env_file.resolve()), warn=not env_file.exists())

    # Secrets store
    console.print()
    console.print("  [dim]Secrets store[/dim]")
    try:
        importlib.import_module("cryptography")
        check("Package: cryptography", True)
    except ImportError:
        check("Package: cryptography (optional)", False, "pip install cryptography", warn=True)

    secrets_db = Path.home() / ".aios" / "secrets.db"
    if secrets_db.exists():
        try:
            import aiosqlite

            async def _count_secrets() -> int:
                async with aiosqlite.connect(secrets_db) as db:
                    try:
                        row = await (await db.execute("SELECT COUNT(*) FROM secrets")).fetchone()
                        return row[0] if row else 0
                    except Exception:
                        return 0

            n = asyncio.run(_count_secrets())
            check("Secrets DB", True, f"{secrets_db}  ({n} secret(s))")
        except Exception:
            check("Secrets DB", True, str(secrets_db))
    else:
        check("Secrets DB", False, "not initialised — run: aios secrets set NAME VALUE", warn=True)

    # Summary
    console.print()
    if fail_count == 0 and warn_count == 0:
        console.print("  [green bold]All checks passed.[/green bold] Ready to run agents.\n")
    elif fail_count == 0:
        console.print(f"  [yellow]{warn_count} warning(s)[/yellow] — optional items missing.\n")
    else:
        console.print(f"  [red]{fail_count} error(s)[/red], {warn_count} warning(s) — fix errors before running agents.\n")


def _get_version() -> str:
    try:
        from .. import __version__

        return __version__
    except Exception:
        return "unknown"


# ── bus ──────────────────────────────────────────────────────────────────────

bus_app = typer.Typer(
    name="bus",
    help="Agent-to-agent message bus (pub/sub over SQLite).",
    no_args_is_help=True,
)
app.add_typer(bus_app, name="bus")


@bus_app.command(name="publish")
def bus_publish(
    topic: str = typer.Argument(..., help="Topic name"),
    payload: str = typer.Argument(..., help="Message payload (JSON or plain text)"),
    sender: str = typer.Option("cli", "--sender", "-s", help="Sender name"),
    ttl: int = typer.Option(86_400, "--ttl", help="Time-to-live in seconds (0 = forever)"),
) -> None:
    """Publish a message to a bus topic."""
    from ..bus.store import MessageBus
    import json as _json

    try:
        data = _json.loads(payload)
    except _json.JSONDecodeError:
        data = payload

    async def _pub():
        bus = MessageBus()
        await bus.setup()
        mid = await bus.publish(topic, data, sender=sender, ttl=ttl)
        return mid

    mid = asyncio.run(_pub())
    console.print(f"[green]✓[/green] Published to [bold]{topic}[/bold] (id={mid})")


@bus_app.command(name="list")
def bus_list() -> None:
    """List all bus topics with message counts."""
    from ..bus.store import MessageBus

    async def _list():
        bus = MessageBus()
        await bus.setup()
        return await bus.topics()

    topics = asyncio.run(_list())
    if not topics:
        console.print("[dim]No messages in the bus. Publish with: aios bus publish <topic> <payload>[/dim]")
        return

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="dim", padding=(0, 1))
    t.add_column("Topic", style="bold", min_width=20)
    t.add_column("Messages", justify="right", min_width=10)
    t.add_column("Latest", min_width=14)

    for row in topics:
        t.add_row(row["topic"], str(row["count"]), _fmt_relative(row["last"] or ""))

    console.print("\n[bold]Bus topics[/bold]")
    console.print(t)


@bus_app.command(name="read")
def bus_read(
    topic: str = typer.Argument(..., help="Topic name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of messages to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Poll for new messages (Ctrl-C to stop)"),
) -> None:
    """Read recent messages from a bus topic."""
    from ..bus.store import MessageBus
    import json as _json

    async def _read():
        bus = MessageBus()
        await bus.setup()
        return await bus.latest(topic, n=limit)

    def _print_msgs(msgs):
        for m in msgs:
            age = _fmt_relative(m["created_at"]) if m["created_at"] else "—"
            payload = m["payload"]
            if isinstance(payload, (dict, list)):
                text = _json.dumps(payload, ensure_ascii=False)
            else:
                text = str(payload)
            sender = f"[dim]{m['sender']}[/dim] " if m["sender"] else ""
            console.print(f"  [dim]#{m['id']}[/dim] {sender}[dim]{age}[/dim]")
            console.print(f"    {text}")

    msgs = asyncio.run(_read())
    if not msgs and not follow:
        console.print(f"[dim]No messages on topic [bold]{topic}[/bold][/dim]")
        return

    _print_msgs(msgs)

    if follow:
        cursor = msgs[-1]["id"] if msgs else 0
        console.print(f"\n[dim]Watching [bold]{topic}[/bold]… (Ctrl-C to stop)[/dim]")

        async def _watch():
            nonlocal cursor
            bus = MessageBus()
            await bus.setup()
            while True:
                new_msgs, cursor = await bus.poll(topic, since=cursor)
                if new_msgs:
                    _print_msgs(new_msgs)
                await asyncio.sleep(1.0)

        try:
            asyncio.run(_watch())
        except KeyboardInterrupt:
            pass


@bus_app.command(name="drain")
def bus_drain(
    topic: str = typer.Argument(..., help="Topic to clear"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete all messages on a topic."""
    from ..bus.store import MessageBus

    if not yes:
        typer.confirm(f"Delete all messages on topic '{topic}'?", abort=True)

    async def _drain():
        bus = MessageBus()
        await bus.setup()
        return await bus.drain(topic)

    count = asyncio.run(_drain())
    console.print(f"[green]✓[/green] Drained [bold]{count}[/bold] message(s) from [bold]{topic}[/bold]")


# ── secrets ───────────────────────────────────────────────────────────────────

secrets_app = typer.Typer(
    name="secrets",
    help="Manage encrypted agent secrets.",
    no_args_is_help=True,
)
app.add_typer(secrets_app, name="secrets")


def _secrets_store():
    """Return a SecretsStore or exit with a clear error if cryptography is missing."""
    try:
        from ..secrets import SecretsStore
        return SecretsStore()
    except ImportError:
        console.print("[red]Install cryptography:[/red] pip install cryptography")
        raise typer.Exit(1)


@secrets_app.command(name="set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name (e.g. OPENAI_API_KEY)"),
    value: str = typer.Argument(..., help="Secret value"),
) -> None:
    """Encrypt and store a secret."""
    store = _secrets_store()
    asyncio.run(store.set(name, value))
    console.print(f"[green]✓[/green] Stored [bold]{name}[/bold]")


@secrets_app.command(name="get")
def secrets_get(
    name: str = typer.Argument(..., help="Secret name"),
) -> None:
    """Decrypt and print a secret."""
    store = _secrets_store()
    value = asyncio.run(store.get(name))
    if value is None:
        console.print(f"[dim]Secret [bold]{name}[/bold] not found[/dim]")
        raise typer.Exit(1)
    console.print(value)


@secrets_app.command(name="list")
def secrets_list() -> None:
    """List stored secret names (not values)."""
    store = _secrets_store()
    names = asyncio.run(store.list())
    if not names:
        console.print("[dim]No secrets stored.[/dim]")
        return
    for n in names:
        console.print(f"  [bold]{n}[/bold]")


@secrets_app.command(name="delete")
def secrets_delete(
    name: str = typer.Argument(..., help="Secret name to remove"),
) -> None:
    """Remove a secret from the store."""
    store = _secrets_store()
    asyncio.run(store.delete(name))
    console.print(f"[green]✓[/green] Deleted [bold]{name}[/bold]")


@secrets_app.command(name="import")
def secrets_import(
    env_file: Path = typer.Argument(..., help="Path to .env file to import"),
) -> None:
    """Import all variables from a .env file into the encrypted secrets store."""
    if not env_file.exists():
        console.print(f"[red]File not found:[/red] {env_file}")
        raise typer.Exit(1)
    store = _secrets_store()
    count = asyncio.run(store.import_env_file(env_file))
    console.print(f"[green]✓[/green] Imported [bold]{count}[/bold] secret(s) from {env_file}")


# ── ui ────────────────────────────────────────────────────────────────────────


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Host to bind"),
    port: int = typer.Option(7851, help="Port to bind"),
) -> None:
    """Open the web UI dashboard."""
    import threading
    import webbrowser

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
    template: str = typer.Option("basic", "--template", "-t", help="Starter template: basic | scheduled | research | notifier"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Target directory"),
    list_templates: bool = typer.Option(False, "--list-templates", "-l", help="List available templates"),
) -> None:
    """Scaffold a new agent project.

    \b
    Templates:
      basic      Simple agent with a custom @tool and memory (default)
      scheduled  Agent that runs on a recurring schedule
      research   Web researcher with persistent knowledge base
      notifier   Slack-alerting monitor with @schedule
    """
    from .templates import ENV_TEMPLATE, GITIGNORE_TEMPLATE, TEMPLATES

    if list_templates:
        console.print("\n[bold]Available templates[/bold]\n")
        for name, (_, description) in TEMPLATES.items():
            console.print(f"  [bold cyan]{name:<12}[/bold cyan] {description}")
        console.print()
        return

    if template not in TEMPLATES:
        console.print(f"[red]Unknown template:[/red] {template!r}")
        console.print(f"  Available: {', '.join(TEMPLATES)}")
        raise typer.Exit(1)

    agent_template, _ = TEMPLATES[template]

    target = directory / agent_name
    target.mkdir(parents=True, exist_ok=True)

    class_name = "".join(word.capitalize() for word in agent_name.split("_")) + "Agent"

    agent_file = target / f"{agent_name}.py"
    env_file = target / ".env"
    gitignore = target / ".gitignore"

    files_written = []

    if not agent_file.exists():
        agent_file.write_text(
            agent_template.format(class_name=class_name, agent_name=agent_name, model=model),
            encoding="utf-8",
        )
        files_written.append(str(agent_file))

    if not env_file.exists():
        env_file.write_text(ENV_TEMPLATE.format(agent_name=agent_name), encoding="utf-8")
        files_written.append(str(env_file))

    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_TEMPLATE, encoding="utf-8")
        files_written.append(str(gitignore))

    console.print(
        Panel(
            f"[green]Agent scaffolded[/green]  [dim](template: {template})[/dim]\n\n"
            + "\n".join(f"  [dim]created[/dim]  {f}" for f in files_written)
            + f"\n\n[dim]Next steps:[/dim]\n"
            f"  1. Add your API key to [bold]{target}/.env[/bold]\n"
            f"  2. Edit [bold]{agent_file.name}[/bold] — customise run() and @tool methods\n"
            f"  3. Run it: [bold]aios run {agent_file}[/bold]",
            title="[bold]Ai.os init[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


# ── export ───────────────────────────────────────────────────────────────────


@app.command()
def export(
    agent_name: str = typer.Argument(..., help="Agent name"),
    output: Path = typer.Option(Path(""), "--output", "-o", help="Output file path (default: <name>-memory.json)"),
    include_timeline: bool = typer.Option(True, "--timeline/--no-timeline", help="Include event timeline"),
) -> None:
    """Export an agent's memory to a JSON file for backup or migration."""
    import json
    from datetime import datetime

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    if not db_path.exists():
        console.print(f"[red]No data found for[/red] [bold]{agent_name}[/bold]")
        raise typer.Exit(1)

    async def _dump() -> dict:
        import aiosqlite

        async with aiosqlite.connect(db_path) as db:
            mem_rows = await (await db.execute("SELECT key, value, updated_at FROM memory_long ORDER BY updated_at DESC")).fetchall()
            memory = {r[0]: {"value": _safe_json(r[1]), "updated_at": r[2]} for r in mem_rows}

            timeline = []
            if include_timeline:
                tl_rows = await (await db.execute("SELECT event, data, created_at FROM memory_timeline ORDER BY created_at ASC")).fetchall()
                timeline = [{"event": r[0], "data": _safe_json(r[1]), "at": r[2]} for r in tl_rows]

        return {
            "version": "1",
            "agent": agent_name,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "memory_keys": len(memory),
            "timeline_events": len(timeline),
            "memory": memory,
            "timeline": timeline,
        }

    data = asyncio.run(_dump())

    out_path = output if str(output) else Path(f"{agent_name}-memory.json")
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    console.print(
        Panel(
            f"[green]Memory exported[/green]\n\n"
            f"  agent   [dim]→[/dim]  [bold]{agent_name}[/bold]\n"
            f"  keys    [dim]→[/dim]  {data['memory_keys']}\n"
            f"  events  [dim]→[/dim]  {data['timeline_events']}\n"
            f"  file    [dim]→[/dim]  [bold]{out_path}[/bold]",
            title="[bold]aios export[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


def _safe_json(raw: str) -> object:
    import json
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ── import ───────────────────────────────────────────────────────────────────


@app.command(name="import")
def import_memory(
    agent_name: str = typer.Argument(..., help="Agent name to import into"),
    source: Path = typer.Argument(..., help="Path to the exported JSON file"),
    merge: bool = typer.Option(True, "--merge/--replace", help="Merge with existing memory (default) or replace it"),
) -> None:
    """Import agent memory from a JSON export file.

    Use --replace to clear existing memory before importing.
    """
    import json

    if not source.exists():
        console.print(f"[red]File not found:[/red] {source}")
        raise typer.Exit(1)

    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(1)

    if data.get("version") != "1":
        console.print(f"[yellow]Warning:[/yellow] unknown export version {data.get('version')!r} — proceeding anyway")

    memory: dict = data.get("memory", {})
    if not memory:
        console.print("[dim]No memory keys found in export file[/dim]")
        raise typer.Exit(0)

    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"

    async def _load() -> int:
        import aiosqlite
        from datetime import datetime

        db_path.parent.mkdir(parents=True, exist_ok=True)
        imported = 0

        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS memory_long (
                    agent_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY (agent_id, key)
                )
            """)
            if not merge:
                await db.execute("DELETE FROM memory_long WHERE agent_id = ?", (agent_name,))

            for key, entry in memory.items():
                raw_value = entry["value"] if isinstance(entry, dict) and "value" in entry else entry
                updated_at = entry.get("updated_at", datetime.utcnow().isoformat()) if isinstance(entry, dict) else datetime.utcnow().isoformat()
                await db.execute(
                    "INSERT INTO memory_long (agent_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(agent_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (agent_name, key, json.dumps(raw_value), updated_at),
                )
                imported += 1

            await db.commit()
        return imported

    count = asyncio.run(_load())
    mode = "merged into" if merge else "replaced"
    console.print(
        Panel(
            f"[green]Memory imported[/green]\n\n"
            f"  agent    [dim]→[/dim]  [bold]{agent_name}[/bold]\n"
            f"  keys     [dim]→[/dim]  {count} ({mode} existing memory)\n"
            f"  source   [dim]→[/dim]  [dim]{source}[/dim]",
            title="[bold]aios import[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


# ── test ─────────────────────────────────────────────────────────────────────


@app.command(name="test")
def test_agent(
    agent_file: Path = typer.Argument(..., help="Agent Python file to dry-run"),
    mock_response: str = typer.Option(
        "This is a mock LLM response from aios test.",
        "--mock", "-m",
        help="Text to return from every think() / think_with_tools() call",
    ),
    payload: str = typer.Option(
        "{}",
        "--payload", "-p",
        help="JSON payload to pass to webhook-triggered agents",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress agent logs"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Re-run on file change"),
) -> None:
    """Dry-run an agent without making real LLM calls.

    All think() and think_with_tools() calls return a configurable mock
    response. Tools still execute normally (use --mock to verify tool flow).

    \\b
    Examples:
      aios test myagent.py
      aios test myagent.py --mock "Found 3 issues"
      aios test myagent.py --payload '{"event": "push"}'
      aios test myagent.py --watch
    """
    import importlib.util
    import json as _json
    import logging
    import traceback
    from unittest.mock import AsyncMock, patch

    if not agent_file.exists():
        console.print(f"[red]File not found:[/red] {agent_file}")
        raise typer.Exit(1)

    try:
        payload_dict = _json.loads(payload)
    except _json.JSONDecodeError as e:
        console.print(f"[red]Invalid --payload JSON:[/red] {e}")
        raise typer.Exit(1)

    log_level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")

    console.print(f"[dim]Loading[/dim] [bold]{agent_file}[/bold] [dim](dry-run)[/dim]")

    # Track calls
    tool_calls: list[dict] = []
    think_calls: list[str] = []

    _orig_call = None

    async def _mock_think(self_agent, prompt: str, context=None) -> str:
        think_calls.append(prompt[:120])
        return mock_response

    async def _mock_think_with_tools(self_agent, prompt: str, context=None, max_iterations: int = 10) -> str:
        think_calls.append(prompt[:120])
        return mock_response

    async def _recording_call(registry_self, name: str, arguments) -> object:
        import json as _j
        args = _j.loads(arguments) if isinstance(arguments, str) else arguments
        tool_calls.append({"tool": name, "args": args})
        return await _orig_call(registry_self, name, arguments)

    # Load the agent module
    spec = importlib.util.spec_from_file_location("_aios_test_agent", agent_file)
    if spec is None or spec.loader is None:
        console.print(f"[red]Cannot load:[/red] {agent_file}")
        raise typer.Exit(1)

    mod = importlib.util.module_from_spec(spec)

    error_msg: str | None = None
    try:
        with (
            patch("aios.agent.Agent.think", _mock_think),
            patch("aios.agent.Agent.think_with_tools", _mock_think_with_tools),
        ):
            spec.loader.exec_module(mod)

            # Find Agent subclass(es)
            from aios import Agent as _Agent
            from aios.triggers import _TRIGGER_MARKER

            agent_classes = [
                v for v in vars(mod).values()
                if isinstance(v, type) and issubclass(v, _Agent) and v is not _Agent
            ]

            if not agent_classes:
                console.print("[red]No Agent subclass found in file.[/red]")
                raise typer.Exit(1)

            for cls in agent_classes:
                from aios.config import load_env
                load_env()

                instance = cls()
                asyncio.run(instance._bootstrap())

                from aios.tools.registry import ToolRegistry
                _orig_call = ToolRegistry.call.__wrapped__ if hasattr(ToolRegistry.call, "__wrapped__") else ToolRegistry.call

                run_method = cls.run
                is_webhook = getattr(run_method, _TRIGGER_MARKER, False) and \
                             getattr(run_method, "__aios_trigger_kind__", "") == "webhook"

                try:
                    if is_webhook:
                        asyncio.run(instance.run(payload_dict))
                    else:
                        asyncio.run(instance.run())
                except Exception as exc:
                    error_msg = traceback.format_exc()

    except SystemExit:
        pass
    except Exception as exc:
        error_msg = traceback.format_exc()

    # ── Report ────────────────────────────────────────────────────────────────
    console.print()

    t = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    t.add_column("", style="dim", width=14)
    t.add_column("")

    t.add_row("Agent file", str(agent_file))
    t.add_row("LLM calls", f"[dim]{len(think_calls)} mocked[/dim]")
    t.add_row("Tool calls", str(len(tool_calls)) if tool_calls else "[dim]0[/dim]")
    t.add_row("Result", "[green]PASS[/green]" if not error_msg else "[red]FAIL[/red]")
    console.print(t)

    if think_calls:
        console.print("[dim]── Think prompts (truncated) ────────────────[/dim]")
        for i, p in enumerate(think_calls[:5], 1):
            console.print(f"  [dim]{i}.[/dim] {p}…" if len(p) == 120 else f"  [dim]{i}.[/dim] {p}")
        if len(think_calls) > 5:
            console.print(f"  [dim]… and {len(think_calls)-5} more[/dim]")

    if tool_calls:
        console.print("[dim]── Tool calls ────────────────────────────────[/dim]")
        for tc in tool_calls[:10]:
            arg_preview = str(tc["args"])[:60]
            console.print(f"  [blue]{tc['tool']}[/blue]  [dim]{arg_preview}[/dim]")

    if error_msg:
        console.print()
        console.print("[red]── Error ──────────────────────────────────────[/red]")
        for line in error_msg.strip().splitlines()[-8:]:
            console.print(f"  [dim]{line}[/dim]")
        if not watch:
            raise typer.Exit(1)

    console.print()

    if watch:
        import hashlib

        def _file_hash(p: Path) -> str:
            return hashlib.md5(p.read_bytes()).hexdigest()

        last_hash = _file_hash(agent_file)
        console.print(f"[dim]Watching[/dim] [bold]{agent_file}[/bold] [dim]— Ctrl+C to stop[/dim]")

        try:
            while True:
                time.sleep(0.5)
                current = _file_hash(agent_file)
                if current != last_hash:
                    last_hash = current
                    console.print(f"\n[dim]── File changed, re-running… ──────────────────[/dim]\n")
                    # Re-invoke this command by running ourselves as a subprocess
                    args = [sys.executable, "-m", "aios", "test", str(agent_file),
                            "--mock", mock_response, "--payload", payload]
                    if quiet:
                        args.append("--quiet")
                    subprocess.run(args)
        except KeyboardInterrupt:
            console.print("\n[dim]Stopped.[/dim]")


# ── stats ────────────────────────────────────────────────────────────────────


# Claude Sonnet 4.6 pricing (USD per 1M tokens)
_COST_INPUT_PER_M = 3.00
_COST_OUTPUT_PER_M = 15.00


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost from token counts."""
    return (prompt_tokens / 1_000_000) * _COST_INPUT_PER_M + \
           (completion_tokens / 1_000_000) * _COST_OUTPUT_PER_M


def _fmt_cost(usd: float) -> str:
    if usd < 0.001:
        return f"${usd*100:.4f}¢"
    if usd < 1.0:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


@app.command()
def stats(
    cost: bool = typer.Option(False, "--cost", "-c", help="Show estimated USD cost column"),
) -> None:
    """Show aggregate statistics for all agents."""

    async def _gather() -> list[dict]:
        import aiosqlite

        result = []
        for a in PM.list_agents():
            name = a["name"]
            db_path = PM.AIOS_DIR / "data" / f"{name}.db"
            row: dict = {
                "name": name,
                "running": a.get("running", False),
                "total": 0, "completed": 0, "failed": 0,
                "avg_dur": None, "last_run": None,
                "memory_keys": 0,
                "total_tokens": 0, "total_llm_calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0,
            }
            if not db_path.exists():
                result.append(row)
                continue
            try:
                async with aiosqlite.connect(db_path) as db:
                    runs = await (await db.execute(
                        "SELECT status, started_at, ended_at, total_tokens, llm_calls, "
                        "prompt_tokens, completion_tokens "
                        "FROM agent_runs WHERE agent_id = ? ORDER BY started_at DESC",
                        (name,),
                    )).fetchall()
                    row["total"] = len(runs)
                    row["completed"] = sum(1 for r in runs if r[0] == "completed")
                    row["failed"] = sum(1 for r in runs if r[0] == "failed")
                    if runs:
                        row["last_run"] = runs[0][1][:19] if runs[0][1] else None
                    durations = []
                    for r in runs:
                        if r[1] and r[2]:
                            try:
                                from datetime import datetime
                                s = (datetime.fromisoformat(r[2]) - datetime.fromisoformat(r[1])).total_seconds()
                                if s >= 0:
                                    durations.append(s)
                            except Exception:
                                pass
                    row["avg_dur"] = sum(durations) / len(durations) if durations else None
                    row["total_tokens"] = sum((r[3] or 0) for r in runs)
                    row["total_llm_calls"] = sum((r[4] or 0) for r in runs)
                    row["prompt_tokens"] = sum((r[5] or 0) for r in runs)
                    row["completion_tokens"] = sum((r[6] or 0) for r in runs)
                    # Fallback: if split tokens not tracked, assume 70/30 split
                    if not row["prompt_tokens"] and row["total_tokens"]:
                        row["prompt_tokens"] = int(row["total_tokens"] * 0.70)
                        row["completion_tokens"] = int(row["total_tokens"] * 0.30)
                    mem = await (await db.execute(
                        "SELECT COUNT(*) FROM memory_long WHERE agent_id = ?", (name,)
                    )).fetchone()
                    row["memory_keys"] = mem[0] if mem else 0
            except Exception:
                pass
            result.append(row)
        return result

    rows = asyncio.run(_gather())

    if not rows:
        console.print("[dim]No agents found. Run [bold]aios run agent.py -d[/bold] to start one.[/dim]")
        return

    # ── Summary header ─────────────────────────────────────────────────────────
    running = sum(1 for r in rows if r["running"])
    total_runs = sum(r["total"] for r in rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_cost = sum(_estimate_cost(r["prompt_tokens"], r["completion_tokens"]) for r in rows)

    cost_part = f"   [yellow]{_fmt_cost(total_cost)}[/yellow] est. cost" if total_tokens else ""
    console.print()
    console.print(
        Panel(
            f"  [bold]{len(rows)}[/bold] agents   "
            f"[green bold]{running}[/green bold] running   "
            f"[bold]{total_runs}[/bold] total runs   "
            f"[dim]{_fmt_tokens(total_tokens)} tokens[/dim]"
            f"{cost_part}",
            title="[bold]Ai.os  Stats[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )
    console.print()

    # ── Per-agent table ────────────────────────────────────────────────────────
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="dim", padding=(0, 1))
    t.add_column("", width=2)
    t.add_column("Agent", style="bold", min_width=14)
    t.add_column("Runs", justify="right", min_width=5)
    t.add_column("OK", justify="right", style="green", min_width=4)
    t.add_column("Fail", justify="right", min_width=4)
    t.add_column("Avg dur", justify="right", min_width=8)
    t.add_column("Tokens", justify="right", min_width=8)
    if cost:
        t.add_column("Est. cost", justify="right", min_width=10, style="yellow")
    t.add_column("Mem keys", justify="right", min_width=8)
    t.add_column("Last run", min_width=16)

    for r in rows:
        dot = Text("● ", style="green bold") if r["running"] else Text("● ", style="dim")
        fail_style = "red" if r["failed"] else "dim"
        avg = _fmt_dur(r["avg_dur"]) if r["avg_dur"] is not None else Text("—", style="dim")
        tokens = _fmt_tokens(r["total_tokens"]) if r["total_tokens"] else Text("—", style="dim")
        last = _fmt_relative(r["last_run"]) if r["last_run"] else Text("never", style="dim")
        row_data = [
            dot,
            r["name"],
            str(r["total"]) if r["total"] else Text("0", style="dim"),
            str(r["completed"]) if r["completed"] else Text("0", style="dim"),
            Text(str(r["failed"]), style=fail_style),
            avg,
            tokens,
        ]
        if cost:
            c = _estimate_cost(r["prompt_tokens"], r["completion_tokens"])
            row_data.append(_fmt_cost(c) if c else Text("—", style="dim"))
        row_data.extend([
            str(r["memory_keys"]) if r["memory_keys"] else Text("0", style="dim"),
            last,
        ])
        t.add_row(*row_data)

    console.print(t)
    if cost:
        console.print(
            f"[dim]Cost estimates use Claude Sonnet 4.6 pricing "
            f"(${_COST_INPUT_PER_M}/M input · ${_COST_OUTPUT_PER_M}/M output). "
            f"Actual cost depends on the model configured per agent.[/dim]\n"
        )


def _fmt_dur(seconds: float) -> str:
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s//60}m {s%60}s"
    return f"{s//3600}h {(s%3600)//60}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _fmt_relative(iso: str) -> str:
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(iso)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        diff = (datetime.now(timezone.utc) - d).total_seconds()
        if diff < 60:
            return "just now"
        if diff < 3600:
            return f"{int(diff//60)}m ago"
        if diff < 86400:
            return f"{int(diff//3600)}h ago"
        return f"{int(diff//86400)}d ago"
    except Exception:
        return iso[:16]


# ── snapshot / rollback ──────────────────────────────────────────────────────


def _snapshot_db(name: str) -> "Path":
    return PM.AIOS_DIR / "data" / f"{name}.db"


@app.command()
def snapshot(
    agent_name: str = typer.Argument(..., help="Agent name"),
    tag: str = typer.Option("", "--tag", "-t", help="Snapshot tag (default: timestamp)"),
) -> None:
    """Save a named memory snapshot for an agent.

    Snapshots capture the full long-term memory and can be restored later with
    [bold]aios rollback[/bold]. Useful before experimenting with prompts.
    """
    import aiosqlite
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tag = tag or ts

    async def _snap() -> int:
        db_path = _snapshot_db(agent_name)
        if not db_path.exists():
            return -1
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE IF NOT EXISTS agent_snapshots "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, "
                " agent_id TEXT NOT NULL, tag TEXT NOT NULL, "
                " memory_json TEXT NOT NULL, created_at TEXT NOT NULL, "
                " UNIQUE(agent_id, tag))"
            )
            rows = await (await db.execute(
                "SELECT key, value FROM memory_long WHERE agent_id = ?", (agent_name,)
            )).fetchall()
            memory_json = json.dumps([{"key": r[0], "value": r[1]} for r in rows])
            await db.execute(
                "INSERT INTO agent_snapshots (agent_id, tag, memory_json, created_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(agent_id, tag) DO UPDATE SET memory_json=excluded.memory_json, created_at=excluded.created_at",
                (agent_name, tag, memory_json, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
            return len(rows)

    import json
    count = asyncio.run(_snap())
    if count == -1:
        console.print(f"[red]No database found for agent[/red] [bold]{agent_name}[/bold]")
        raise typer.Exit(1)
    console.print(
        f"[green]✓[/green] Snapshot [bold]{tag}[/bold] saved "
        f"([dim]{count} memory keys[/dim])\n"
        f"  Restore with: [dim]aios rollback {agent_name} {tag}[/dim]"
    )


@app.command()
def snapshots(
    agent_name: str = typer.Argument(..., help="Agent name"),
) -> None:
    """List saved memory snapshots for an agent."""
    import aiosqlite

    async def _list() -> list[tuple]:
        db_path = _snapshot_db(agent_name)
        if not db_path.exists():
            return []
        try:
            async with aiosqlite.connect(db_path) as db:
                rows = await (await db.execute(
                    "SELECT tag, created_at, length(memory_json) "
                    "FROM agent_snapshots WHERE agent_id = ? "
                    "ORDER BY created_at DESC", (agent_name,)
                )).fetchall()
                return rows
        except Exception:
            return []

    rows = asyncio.run(_list())
    if not rows:
        console.print(f"[dim]No snapshots for [bold]{agent_name}[/bold]. "
                      f"Run [bold]aios snapshot {agent_name}[/bold] first.[/dim]")
        return

    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="dim", padding=(0, 1))
    t.add_column("Tag", style="bold", min_width=20)
    t.add_column("Created", min_width=14)
    t.add_column("Size", justify="right", min_width=8)

    import json
    for tag, created_at, sz in rows:
        age = _fmt_relative(created_at) if created_at else "—"
        t.add_row(tag, age, f"{sz // 1024}k" if sz >= 1024 else f"{sz}b")

    console.print(f"\n[bold]Snapshots[/bold] — {agent_name}")
    console.print(t)
    console.print(f"[dim]Restore with: aios rollback {agent_name} <tag>[/dim]\n")


@app.command()
def rollback(
    agent_name: str = typer.Argument(..., help="Agent name"),
    tag: str = typer.Argument(..., help="Snapshot tag to restore"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Restore a memory snapshot for an agent.

    [yellow]Warning:[/yellow] This overwrites the agent's current long-term memory.
    Run [bold]aios snapshot[/bold] first to preserve the current state.
    """
    import aiosqlite
    import json

    async def _load_snap() -> list[dict] | None:
        db_path = _snapshot_db(agent_name)
        if not db_path.exists():
            return None
        try:
            async with aiosqlite.connect(db_path) as db:
                row = await (await db.execute(
                    "SELECT memory_json FROM agent_snapshots "
                    "WHERE agent_id = ? AND tag = ?", (agent_name, tag)
                )).fetchone()
                if not row:
                    return None
                return json.loads(row[0])
        except Exception:
            return None

    async def _restore(entries: list[dict]) -> int:
        from datetime import datetime, timezone
        db_path = _snapshot_db(agent_name)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM memory_long WHERE agent_id = ?", (agent_name,))
            now = datetime.now(timezone.utc).isoformat()
            for e in entries:
                await db.execute(
                    "INSERT INTO memory_long (agent_id, key, value, updated_at) VALUES (?,?,?,?)",
                    (agent_name, e["key"], e["value"], now),
                )
            await db.commit()
        return len(entries)

    entries = asyncio.run(_load_snap())
    if entries is None:
        console.print(f"[red]Snapshot [bold]{tag}[/bold] not found for agent [bold]{agent_name}[/bold][/red]")
        console.print(f"[dim]Run [bold]aios snapshots {agent_name}[/bold] to list available snapshots.[/dim]")
        raise typer.Exit(1)

    if not yes:
        console.print(
            f"[yellow]⚠[/yellow]  This will overwrite [bold]{agent_name}[/bold]'s "
            f"current memory with snapshot [bold]{tag}[/bold] ({len(entries)} keys)."
        )
        typer.confirm("Continue?", abort=True)

    restored = asyncio.run(_restore(entries))
    console.print(f"[green]✓[/green] Restored [bold]{len(entries)}[/bold] memory keys from snapshot [bold]{tag}[/bold]")


# ── mcp ──────────────────────────────────────────────────────────────────────


@app.command()
def mcp(
    agent_file: Path = typer.Argument(..., help="Path to the agent Python file"),
    port: int = typer.Option(3000, "--port", "-p", help="Port to listen on"),
    name: str = typer.Option("", "--name", "-n", help="Override tool name (default: agent filename stem)"),
    description: str = typer.Option("", "--description", "-d", help="Override tool description"),
    stdio: bool = typer.Option(False, "--stdio", help="Use stdio transport instead of HTTP/SSE"),
) -> None:
    """Expose an agent as an MCP (Model Context Protocol) server.

    Lets Claude Desktop, Cursor, and any MCP host call your agent as a tool.

    [bold]HTTP/SSE mode (default):[/bold]
      aios mcp researcher.py --port 3000

    Add to Claude Desktop config (~/.config/claude/claude_desktop_config.json):
      [dim]{"mcpServers": {"researcher": {"url": "http://localhost:3000/sse"}}}[/dim]

    [bold]stdio mode (direct subprocess):[/bold]
      aios mcp researcher.py --stdio

    Add to Claude Desktop config:
      [dim]{"mcpServers": {"researcher": {"command": "aios", "args": ["mcp", "researcher.py", "--stdio"]}}}[/dim]
    """
    from ..mcp.server import run_mcp_server

    if not agent_file.exists():
        console.print(f"[red]File not found:[/red] {agent_file}")
        raise typer.Exit(1)

    tool_name = name or agent_file.stem
    tool_desc = description

    if not stdio:
        console.print(
            Panel(
                f"  [green]MCP server starting[/green]\n\n"
                f"  tool      [dim]→[/dim]  [bold]{tool_name}[/bold]\n"
                f"  transport [dim]→[/dim]  HTTP/SSE\n"
                f"  url       [dim]→[/dim]  [bold]http://localhost:{port}/sse[/bold]\n\n"
                f"  [dim]Add to Claude Desktop:[/dim]\n"
                f'  [dim]{{"mcpServers": {{"{tool_name}": {{"url": "http://localhost:{port}/sse"}}}}}}[/dim]',
                title="[bold]Ai.os MCP[/bold]",
                border_style="dim",
                padding=(0, 1),
            )
        )

    asyncio.run(run_mcp_server(
        agent_file=agent_file.resolve(),
        tool_name=tool_name,
        tool_description=tool_desc,
        port=port,
        stdio=stdio,
    ))


# ── eval ─────────────────────────────────────────────────────────────────────


@app.command()
def eval(
    agent_file: Path = typer.Argument(..., help="Path to the agent Python file"),
    suite: Path = typer.Option(
        Path(""), "--suite", "-s",
        help="Path to eval YAML file (default: <agent_stem>.eval.yaml)",
    ),
    update: bool = typer.Option(False, "--update", "-u", help="Update golden outputs in the YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full diff for failures"),
    fail_fast: bool = typer.Option(False, "--fail-fast", "-x", help="Stop after first failure"),
    junit: Path = typer.Option(Path(""), "--junit", help="Write JUnit XML results to this path (for CI)"),
) -> None:
    """Run golden-file regression tests against an agent (LLM mocked).

    Each case in the YAML specifies an optional mock LLM response and the
    expected output. On first run use [bold]--update[/bold] to capture outputs as the
    new golden baseline.

    [bold]Suite format (agent.eval.yaml):[/bold]

      cases:
        - name: basic question
          mock_response: "Paris"          # what the mocked LLM returns
          memory:                         # optional: seed memory keys
            country: France
          expected: "Paris"              # substring match (or exact with exact: true)
          exact: false
    """
    from ..eval.runner import run_eval_suite, EvalResult

    if not agent_file.exists():
        console.print(f"[red]File not found:[/red] {agent_file}")
        raise typer.Exit(1)

    suite_path = suite if suite != Path("") else agent_file.with_suffix(".eval.yaml")
    if not suite_path.exists():
        # Create a starter suite
        starter = (
            f"# Eval suite for {agent_file.name}\n"
            f"# Run: aios eval {agent_file} --update  (captures first golden outputs)\n\n"
            f"cases:\n"
            f"  - name: example\n"
            f"    mock_response: \"Hello from the mock LLM\"\n"
            f"    expected: \"Hello\"   # substring match\n"
            f"    exact: false\n"
        )
        suite_path.write_text(starter)
        console.print(f"[yellow]Created starter suite:[/yellow] {suite_path}")
        console.print(f"[dim]Edit it, then run:[/dim] aios eval {agent_file} --update")
        return

    results: list[EvalResult] = asyncio.run(
        run_eval_suite(agent_file.resolve(), suite_path, update=update)
    )

    if not results:
        console.print("[dim]No cases found in suite.[/dim]")
        return

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    skipped = [r for r in results if r.skipped]

    console.print()
    for r in results:
        if r.skipped:
            icon = Text("  SKIP ", style="dim")
        elif r.passed:
            icon = Text("  PASS ", style="green bold")
        else:
            icon = Text("  FAIL ", style="red bold")

        console.print(icon, r.name, end="")
        if r.duration is not None:
            console.print(f"  [dim]{r.duration:.2f}s[/dim]", end="")
        console.print()

        if not r.passed and not r.skipped:
            console.print(f"    [dim]expected:[/dim] {r.expected!r}")
            console.print(f"    [dim]got:     [/dim] {r.actual!r}")
            if r.error:
                console.print(f"    [red]error:[/red] {r.error}")
            if fail_fast:
                break

    console.print()
    total = len(results)
    summary = (
        f"[bold]{total}[/bold] cases  "
        f"[green bold]{len(passed)} passed[/green bold]  "
        f"[red]{len(failed)} failed[/red]  "
        f"[dim]{len(skipped)} skipped[/dim]"
    )
    if update:
        summary += "  [yellow bold]— golden outputs updated[/yellow bold]"
    console.print(
        Panel(summary, title="[bold]aios eval[/bold]", border_style="dim", padding=(0, 1))
    )
    console.print()

    if junit != Path(""):
        _write_junit(junit, results, agent_file.stem)
        console.print(f"[dim]JUnit XML written to[/dim] {junit}")

    if failed:
        raise typer.Exit(1)


def _write_junit(path: Path, results: "list", suite_name: str) -> None:
    """Write JUnit XML that CI systems (GitHub Actions, GitLab, Jenkins) can parse."""
    from xml.etree.ElementTree import Element, SubElement, tostring
    from xml.dom.minidom import parseString

    total = len(results)
    failures = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    time_total = sum(r.duration or 0 for r in results)

    suite = Element("testsuite", {
        "name": suite_name,
        "tests": str(total),
        "failures": str(failures),
        "skipped": str(skipped),
        "time": f"{time_total:.3f}",
    })

    for r in results:
        tc = SubElement(suite, "testcase", {
            "name": r.name,
            "classname": suite_name,
            "time": f"{r.duration or 0:.3f}",
        })
        if r.skipped:
            SubElement(tc, "skipped")
        elif not r.passed:
            msg = r.error or f"expected {r.expected!r} in {r.actual!r}"
            fail = SubElement(tc, "failure", {"message": msg[:120]})
            fail.text = f"Expected: {r.expected!r}\nGot:      {r.actual!r}"
            if r.error:
                fail.text += f"\nError: {r.error}"

    xml_str = parseString(tostring(suite, encoding="unicode")).toprettyxml(indent="  ")
    path.write_text(xml_str, encoding="utf-8")


# ── publish ──────────────────────────────────────────────────────────────────


@app.command()
def publish(
    agent_file: Path = typer.Argument(..., help="Agent Python file to package"),
    package_name: str = typer.Option("", "--name", "-n", help="PyPI package name (default: aios-<agent-name>)"),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory for package scaffold"),
    push: bool = typer.Option(False, "--push", help="Build and push to PyPI after scaffolding (requires twine)"),
) -> None:
    """Scaffold a shareable pip package from an agent file.

    Creates a minimal Python package so others can install and run your agent:

    \\b
      pip install aios-myagent
      python -m aios_myagent

    \\b
    Examples:
      aios publish myagent.py
      aios publish myagent.py --name aios-myagent --push
    """
    agent_name = agent_file.stem
    pkg_name = package_name or f"aios-{agent_name}"
    pkg_module = pkg_name.replace("-", "_")

    pkg_dir = output / pkg_module
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # Copy agent file into package
    agent_src = agent_file.read_text(encoding="utf-8") if agent_file.exists() else f"# {agent_name} agent\n"
    (pkg_dir / "agent.py").write_text(agent_src, encoding="utf-8")

    # __init__.py
    (pkg_dir / "__init__.py").write_text(
        f'"""Ai.os agent package: {pkg_name}."""\n'
        f'from .agent import *  # noqa: F401,F403\n',
        encoding="utf-8",
    )

    # __main__.py — lets `python -m <package>` work
    (pkg_dir / "__main__.py").write_text(
        f'"""Entry point: python -m {pkg_module}"""\n'
        f"from .agent import *  # noqa: F401,F403\n"
        f"import importlib, sys\n"
        f"mod = importlib.import_module('.agent', '{pkg_module}')\n"
        f"cls = next((v for v in vars(mod).values() if isinstance(v, type) and hasattr(v, 'launch')), None)\n"
        f"if cls is None:\n"
        f"    print('No Agent subclass found in agent.py'); sys.exit(1)\n"
        f"cls.launch()\n",
        encoding="utf-8",
    )

    # pyproject.toml
    from .. import __version__
    pyproject = f"""\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{pkg_name}"
version = "0.1.0"
description = "Ai.os agent: {agent_name}"
requires-python = ">=3.10"
dependencies = ["aios-runtime>={__version__}"]

[project.scripts]
{pkg_name} = "{pkg_module}.__main__:main"
"""
    (output / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    # README
    readme = f"""\
# {pkg_name}

An [Ai.os](https://github.com/kolan51/Ai.os) agent.

## Install

```bash
pip install {pkg_name}
```

## Run

```bash
python -m {pkg_module}
```

## Requirements

Set your LLM API key in `.env` (see `.env.example` in [aios-runtime](https://pypi.org/project/aios-runtime/)).
"""
    (output / "README.md").write_text(readme, encoding="utf-8")

    files = [
        f"{pkg_module}/__init__.py",
        f"{pkg_module}/agent.py",
        f"{pkg_module}/__main__.py",
        "pyproject.toml",
        "README.md",
    ]
    rows = "\n".join(f"  [dim]→[/dim]  [bold]{output}/{f}[/bold]" for f in files)

    if push:
        console.print("[dim]Building...[/dim]")
        result = subprocess.run(
            [sys.executable, "-m", "build", str(output)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            console.print(f"[red]Build failed:[/red]\n{result.stderr}")
            raise typer.Exit(1)
        result2 = subprocess.run(
            [sys.executable, "-m", "twine", "upload", str(output / "dist" / "*")],
            capture_output=True, text=True,
        )
        if result2.returncode != 0:
            console.print(f"[red]Upload failed:[/red]\n{result2.stderr}")
            raise typer.Exit(1)
        console.print(f"[green]Published[/green] [bold]{pkg_name}[/bold] to PyPI")
        return

    console.print(
        Panel(
            f"[green]Package scaffold created[/green]\n\n"
            f"{rows}\n\n"
            f"[bold]To publish:[/bold]\n"
            f"  [dim]pip install build twine[/dim]\n"
            f"  [cyan]python -m build {output}[/cyan]\n"
            f"  [cyan]python -m twine upload {output}/dist/*[/cyan]\n\n"
            f"[bold]Or one-shot:[/bold]  [cyan]aios publish {agent_file} --push[/cyan]",
            title=f"[bold]aios publish[/bold]  →  {pkg_name}",
            border_style="dim",
            padding=(0, 1),
        )
    )


# ── deploy ───────────────────────────────────────────────────────────────────


@app.command()
def deploy(
    agent_file: Path = typer.Argument(Path("."), help="Agent file or project directory (default: current dir)"),
    output: Path = typer.Option(Path("deploy"), "--output", "-o", help="Output directory for deploy bundle"),
    platform: str = typer.Option("docker", "--platform", "-p", help="Target platform: docker | fly | systemd"),
    port: int = typer.Option(8000, "--port", help="Web UI port"),
) -> None:
    """Generate a production deployment bundle for self-hosting.

    Outputs a Dockerfile, docker-compose.yml, and startup scripts.
    Supports Docker Compose (default), Fly.io, and Linux systemd.

    \\b
    Examples:
      aios deploy                        # Docker Compose bundle in ./deploy/
      aios deploy myagent.py -p fly      # Fly.io config
      aios deploy -p systemd             # systemd service unit
    """
    output.mkdir(parents=True, exist_ok=True)

    agent_name = agent_file.stem if agent_file.suffix == ".py" else "agent"
    agent_rel = str(agent_file) if agent_file.suffix == ".py" else "."

    files: dict[str, str] = {}

    if platform in ("docker", "fly"):
        files["Dockerfile"] = _dockerfile(agent_rel)
        files[".dockerignore"] = _dockerignore()

    if platform == "docker":
        files["docker-compose.yml"] = _docker_compose(agent_name, agent_rel, port)
        files["deploy.sh"] = _deploy_sh(agent_name)

    elif platform == "fly":
        files["fly.toml"] = _fly_toml(agent_name, port)
        files["deploy.sh"] = _fly_deploy_sh(agent_name)

    elif platform == "systemd":
        files[f"aios-{agent_name}.service"] = _systemd_unit(agent_name, agent_rel)
        files["install-service.sh"] = _systemd_install_sh(agent_name)

    else:
        console.print(f"[red]Unknown platform:[/red] {platform!r}  (choose: docker | fly | systemd)")
        raise typer.Exit(1)

    written = []
    for fname, content in files.items():
        dest = output / fname
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        written.append(fname)

    rows = "\n".join(f"  [dim]→[/dim]  [bold]{output}/{f}[/bold]" for f in written)
    next_step = {
        "docker": f"cd {output} && docker compose up -d",
        "fly":    f"cd {output} && fly launch --no-deploy && fly secrets set ... && fly deploy",
        "systemd": f"sudo bash {output}/install-service.sh",
    }[platform]

    console.print(
        Panel(
            f"[green]Deploy bundle created[/green]  [dim]({platform})[/dim]\n\n"
            f"{rows}\n\n"
            f"[bold]Next:[/bold]\n  [cyan]{next_step}[/cyan]",
            title="[bold]aios deploy[/bold]",
            border_style="dim",
            padding=(0, 1),
        )
    )


# ── deploy templates ──────────────────────────────────────────────────────────


def _dockerfile(agent_rel: str) -> str:
    copy_line = f"COPY {agent_rel} /app/{agent_rel}" if agent_rel != "." else "COPY . /app/"
    return f"""\
# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl && rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY pyproject.toml* setup.cfg* requirements*.txt* ./
RUN pip install --no-cache-dir aios-runtime || pip install --no-cache-dir -e . 2>/dev/null || true

# Copy agent code
{copy_line}

# Data directory (mount a volume here for persistence)
RUN mkdir -p /root/.aios/data

ENV AIOS_LOG_LEVEL=INFO

EXPOSE 8000

CMD ["python", "/app/{agent_rel}"]
"""


def _dockerignore() -> str:
    return """\
__pycache__/
*.pyc
*.pyo
*.pyd
.Python
.env
.env.*
*.env
.git/
.github/
deploy/
*.db
*.log
dist/
build/
*.egg-info/
.venv/
venv/
node_modules/
.DS_Store
"""


def _docker_compose(agent_name: str, agent_rel: str, port: int) -> str:
    return f"""\
# Ai.os — Docker Compose deployment
# Copy .env.example to .env and fill in your keys before running.
#
#   docker compose up -d          # start
#   docker compose logs -f        # stream logs
#   docker compose down           # stop (data preserved in volume)

services:
  {agent_name}:
    build: .
    container_name: aios-{agent_name}
    restart: unless-stopped
    env_file: ../.env
    volumes:
      - aios-data:/root/.aios/data   # persistent memory + checkpoints
    command: ["python", "/app/{agent_rel}"]

  ui:
    build: .
    container_name: aios-ui
    restart: unless-stopped
    env_file: ../.env
    ports:
      - "{port}:{port}"
    volumes:
      - aios-data:/root/.aios/data
    command: ["aios", "ui", "--host", "0.0.0.0", "--port", "{port}"]

volumes:
  aios-data:
    name: aios-{agent_name}-data
"""


def _deploy_sh(agent_name: str) -> str:
    return f"""\
#!/usr/bin/env bash
# One-command deploy for aios-{agent_name}
# Usage: bash deploy.sh
set -euo pipefail

if [ ! -f ../.env ]; then
  echo "ERROR: ../.env not found. Copy ../.env.example to ../.env and fill in your keys."
  exit 1
fi

echo "Building and starting aios-{agent_name}..."
docker compose pull --quiet 2>/dev/null || true
docker compose up --build -d
echo ""
echo "✓ Running. Logs:"
echo "  docker compose logs -f"
echo ""
echo "✓ Web UI:"
echo "  http://localhost:8000"
"""


def _fly_toml(agent_name: str, port: int) -> str:
    return f"""\
# Ai.os — Fly.io deployment
# 1. Install flyctl:  curl -L https://fly.io/install.sh | sh
# 2. fly launch --no-deploy
# 3. fly secrets set ANTHROPIC_API_KEY=sk-... (and any other keys from .env)
# 4. fly deploy

app = "aios-{agent_name}"
primary_region = "ams"

[build]

[env]
  AIOS_LOG_LEVEL = "INFO"

[http_service]
  internal_port = {port}
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1

[mounts]
  source = "aios_data"
  destination = "/root/.aios/data"
"""


def _fly_deploy_sh(agent_name: str) -> str:
    return f"""\
#!/usr/bin/env bash
# Deploy aios-{agent_name} to Fly.io
set -euo pipefail

echo "Deploying aios-{agent_name} to Fly.io..."
fly deploy --remote-only
echo ""
echo "✓ Deployed. Logs:"
echo "  fly logs"
"""


def _systemd_unit(agent_name: str, agent_rel: str) -> str:
    return f"""\
# aios-{agent_name}.service — systemd unit for Ai.os agent
# Install: sudo bash install-service.sh

[Unit]
Description=Ai.os agent — {agent_name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nobody
WorkingDirectory=/opt/aios/{agent_name}
EnvironmentFile=/opt/aios/{agent_name}/.env
ExecStart=/usr/local/bin/python /opt/aios/{agent_name}/{agent_rel}
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=aios-{agent_name}

[Install]
WantedBy=multi-user.target
"""


def _systemd_install_sh(agent_name: str) -> str:
    return f"""\
#!/usr/bin/env bash
# Install and start aios-{agent_name} as a systemd service
set -euo pipefail

SERVICE_NAME="aios-{agent_name}"
INSTALL_DIR="/opt/aios/{agent_name}"

echo "Installing $SERVICE_NAME..."
mkdir -p "$INSTALL_DIR"
cp -r ../. "$INSTALL_DIR/"
pip install --quiet aios-runtime

cp "$SERVICE_NAME.service" "/etc/systemd/system/"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo ""
echo "✓ Service installed and started."
echo "  Status:   sudo systemctl status $SERVICE_NAME"
echo "  Logs:     sudo journalctl -u $SERVICE_NAME -f"
echo "  Stop:     sudo systemctl stop $SERVICE_NAME"
"""


# ── workspace ────────────────────────────────────────────────────────────────

workspace_app = typer.Typer(
    name="workspace",
    help="Share agents and memory across machines (team workspaces)",
    no_args_is_help=True,
)
app.add_typer(workspace_app, name="workspace")


def _workspace_config_path() -> Path:
    return Path.home() / ".aios" / "workspace.json"


def _workspace_load_config() -> dict:
    p = _workspace_config_path()
    if not p.exists():
        return {}
    import json as _json
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _workspace_save_config(cfg: dict) -> None:
    p = _workspace_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    p.write_text(_json.dumps(cfg, indent=2), encoding="utf-8")


@workspace_app.command()
def init(
    name: str = typer.Argument(..., help="Workspace name (identifier for this team)"),
    share_dir: str = typer.Option("", "--dir", "-d", help="Shared directory path (e.g. a mounted network share or Dropbox folder)"),
) -> None:
    """Initialise a team workspace backed by a shared directory.

    \\b
    Examples:
      aios workspace init myteam --dir /mnt/shared/aios
      aios workspace init myteam --dir ~/Dropbox/aios-workspace
    """
    if not share_dir:
        console.print("[red]--dir is required.[/red] Point it to any shared directory (network share, Dropbox, etc.)")
        raise typer.Exit(1)
    d = Path(share_dir).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    cfg = _workspace_load_config()
    cfg["name"] = name
    cfg["dir"] = str(d)
    _workspace_save_config(cfg)
    console.print(f"[green]✓[/green] Workspace [bold]{name}[/bold] initialised → [dim]{d}[/dim]")
    console.print("  Push an agent:  [bold]aios workspace push <agentname>[/bold]")
    console.print("  Pull an agent:  [bold]aios workspace pull <agentname>[/bold]")


@workspace_app.command()
def push(
    agent_name: str = typer.Argument(..., help="Agent name to push to the workspace"),
    no_timeline: bool = typer.Option(False, "--no-timeline", help="Exclude timeline events"),
) -> None:
    """Push an agent's memory to the shared workspace directory."""
    import json as _json
    cfg = _workspace_load_config()
    if not cfg.get("dir"):
        console.print("[red]No workspace configured.[/red] Run: aios workspace init <name> --dir <path>")
        raise typer.Exit(1)
    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"
    if not db_path.exists():
        console.print(f"[red]Agent not found:[/red] {agent_name}")
        raise typer.Exit(1)

    async def _dump() -> dict:
        import aiosqlite
        data: dict = {"agent": agent_name, "memory": {}, "timeline": []}
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT key, value FROM memory_long ORDER BY updated_at DESC")).fetchall()
                for k, v in rows:
                    try:
                        data["memory"][k] = _json.loads(v)
                    except Exception:
                        data["memory"][k] = v
            except Exception:
                pass
            if not no_timeline:
                try:
                    rows = await (await db.execute(
                        "SELECT event_type, data, created_at FROM memory_timeline ORDER BY created_at DESC LIMIT 500"
                    )).fetchall()
                    for r in rows:
                        try:
                            data["timeline"].append({"event": r[0], "data": _json.loads(r[1] or "{}"), "at": r[2]})
                        except Exception:
                            data["timeline"].append({"event": r[0], "data": r[1], "at": r[2]})
                except Exception:
                    pass
        return data

    payload = asyncio.run(_dump())
    out_dir = Path(cfg["dir"]) / "agents"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{agent_name}.json"
    out_file.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    mem_count = len(payload["memory"])
    tl_count = len(payload["timeline"])
    console.print(f"[green]✓[/green] Pushed [bold]{agent_name}[/bold]  [dim]({mem_count} memory keys, {tl_count} timeline events)[/dim]")
    console.print(f"  [dim]{out_file}[/dim]")


@workspace_app.command()
def pull(
    agent_name: str = typer.Argument(..., help="Agent name to pull from the workspace"),
    replace: bool = typer.Option(False, "--replace", help="Replace existing memory instead of merging"),
) -> None:
    """Pull an agent's memory from the shared workspace directory."""
    import json as _json
    cfg = _workspace_load_config()
    if not cfg.get("dir"):
        console.print("[red]No workspace configured.[/red] Run: aios workspace init <name> --dir <path>")
        raise typer.Exit(1)
    in_file = Path(cfg["dir"]) / "agents" / f"{agent_name}.json"
    if not in_file.exists():
        console.print(f"[red]No workspace snapshot for:[/red] {agent_name}")
        raise typer.Exit(1)

    payload = _json.loads(in_file.read_text(encoding="utf-8"))
    db_path = PM.AIOS_DIR / "data" / f"{agent_name}.db"

    async def _restore() -> tuple[int, int]:
        import aiosqlite
        from datetime import datetime
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS memory_long (agent_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY(agent_id, key))")
            await db.execute("CREATE TABLE IF NOT EXISTS memory_timeline (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, event_type TEXT, data TEXT, created_at TEXT)")
            if replace:
                await db.execute("DELETE FROM memory_long")
            mem_count = 0
            for k, v in payload.get("memory", {}).items():
                await db.execute(
                    "INSERT INTO memory_long (agent_id, key, value, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(agent_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (agent_name, k, _json.dumps(v), datetime.utcnow().isoformat()),
                )
                mem_count += 1
            tl_count = 0
            for ev in payload.get("timeline", []):
                await db.execute(
                    "INSERT INTO memory_timeline (agent_id, event_type, data, created_at) VALUES (?,?,?,?)",
                    (agent_name, ev.get("event",""), _json.dumps(ev.get("data",{})), ev.get("at","")),
                )
                tl_count += 1
            await db.commit()
        return mem_count, tl_count

    mem, tl = asyncio.run(_restore())
    mode = "[dim](replace)[/dim]" if replace else "[dim](merge)[/dim]"
    console.print(f"[green]✓[/green] Pulled [bold]{agent_name}[/bold] {mode}  [dim]({mem} memory keys, {tl} timeline events)[/dim]")


@workspace_app.command(name="list")
def workspace_list() -> None:
    """List agents available in the shared workspace."""
    import json as _json
    cfg = _workspace_load_config()
    if not cfg.get("dir"):
        console.print("[dim]No workspace configured. Run: aios workspace init <name> --dir <path>[/dim]")
        return
    agents_dir = Path(cfg["dir"]) / "agents"
    if not agents_dir.exists():
        console.print(f"[dim]No agents pushed yet to workspace [bold]{cfg.get('name','')}[/bold][/dim]")
        return
    files = sorted(agents_dir.glob("*.json"))
    if not files:
        console.print("[dim]Workspace is empty.[/dim]")
        return
    table = Table(box=box.SIMPLE, header_style="bold dim", padding=(0, 1))
    table.add_column("Agent", style="bold")
    table.add_column("Memory keys", justify="right")
    table.add_column("Timeline events", justify="right")
    table.add_column("File", style="dim")
    for f in files:
        try:
            d = _json.loads(f.read_text(encoding="utf-8"))
            table.add_row(f.stem, str(len(d.get("memory", {}))), str(len(d.get("timeline", []))), str(f))
        except Exception:
            table.add_row(f.stem, "?", "?", str(f))
    console.print(f"\n[bold]Workspace:[/bold] {cfg.get('name', '?')}  [dim]→ {cfg['dir']}[/dim]\n")
    console.print(table)


@workspace_app.command()
def status_ws() -> None:
    """Show current workspace configuration."""
    cfg = _workspace_load_config()
    if not cfg:
        console.print("[dim]No workspace configured.[/dim]")
        return
    console.print(f"[bold]Workspace:[/bold] {cfg.get('name', '—')}")
    console.print(f"  Dir: [dim]{cfg.get('dir', '—')}[/dim]")


# ── version ───────────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Show Ai.os version."""
    from .. import __version__

    console.print(f"[bold]Ai.os[/bold]  [dim]v{__version__}[/dim]")


if __name__ == "__main__":
    app()

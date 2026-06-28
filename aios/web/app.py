from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..runtime.process import ProcessManager as PM


def create_app() -> FastAPI:
    app = FastAPI(title="Ai.os", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        agents = PM.list_agents()
        lines: list[str] = []

        # ── aios_agent_running ───────────────────────────────────────────────
        lines.append("# HELP aios_agent_running 1 if the agent process is running, 0 otherwise")
        lines.append("# TYPE aios_agent_running gauge")
        for a in agents:
            lines.append(f'aios_agent_running{{agent="{a["name"]}"}} {1 if a["running"] else 0}')

        # ── per-agent DB stats ───────────────────────────────────────────────
        lines.append("# HELP aios_agent_runs_total Total number of runs recorded")
        lines.append("# TYPE aios_agent_runs_total counter")
        lines.append("# HELP aios_agent_runs_completed_total Runs that completed successfully")
        lines.append("# TYPE aios_agent_runs_completed_total counter")
        lines.append("# HELP aios_agent_runs_failed_total Runs that ended with an error")
        lines.append("# TYPE aios_agent_runs_failed_total counter")
        lines.append("# HELP aios_agent_memory_keys Number of long-term memory keys")
        lines.append("# TYPE aios_agent_memory_keys gauge")
        lines.append("# HELP aios_agent_checkpoints_total Cached tool calls from latest run")
        lines.append("# TYPE aios_agent_checkpoints_total gauge")

        for a in agents:
            name = a["name"]
            db_path = PM.AIOS_DIR / "data" / f"{name}.db"
            if not db_path.exists():
                continue
            try:
                async with aiosqlite.connect(db_path) as db:
                    run_rows = await (await db.execute("SELECT status FROM agent_runs WHERE agent_id = ?", (name,))).fetchall()
                    total = len(run_rows)
                    completed = sum(1 for r in run_rows if r[0] == "completed")
                    failed = sum(1 for r in run_rows if r[0] == "failed")

                    mem_row = await (await db.execute("SELECT COUNT(*) FROM memory_long WHERE agent_id = ?", (name,))).fetchone()
                    mem_keys = mem_row[0] if mem_row else 0

                    run_row = await (await db.execute("SELECT id FROM agent_runs WHERE agent_id = ? ORDER BY started_at DESC LIMIT 1", (name,))).fetchone()
                    cp_count = 0
                    if run_row:
                        cp_row = await (
                            await db.execute(
                                "SELECT COUNT(*) FROM checkpoints WHERE agent_id = ? AND run_id = ?",
                                (name, run_row[0]),
                            )
                        ).fetchone()
                        cp_count = cp_row[0] if cp_row else 0
            except Exception:
                total = completed = failed = mem_keys = cp_count = 0

            q = f'agent="{name}"'
            lines.append(f"aios_agent_runs_total{{{q}}} {total}")
            lines.append(f"aios_agent_runs_completed_total{{{q}}} {completed}")
            lines.append(f"aios_agent_runs_failed_total{{{q}}} {failed}")
            lines.append(f"aios_agent_memory_keys{{{q}}} {mem_keys}")
            lines.append(f"aios_agent_checkpoints_total{{{q}}} {cp_count}")

        body = "\n".join(lines) + "\n"
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
        <rect width="64" height="64" rx="14" fill="#08090F"/>
        <text x="32" y="41" text-anchor="middle" font-size="28" font-family="Arial" font-weight="800" fill="#5B8DF6">A</text>
        </svg>"""
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/api/agents")
    async def list_agents() -> list[dict]:
        agents = PM.list_agents()
        result = []
        for a in agents:
            info = dict(a)
            db_path = PM.AIOS_DIR / "data" / f"{a['name']}.db"
            if db_path.exists():
                info["memory_keys"] = await _count_memory(db_path)
                info["last_run"] = await _last_run(db_path)
            result.append(info)
        return result

    @app.get("/api/agents/{name}/memory")
    async def agent_memory(name: str) -> list[dict]:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return []
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT key, value, updated_at FROM memory_long ORDER BY updated_at DESC LIMIT 100")).fetchall()
                return [{"key": r[0], "value": _preview(r[1]), "updated_at": r[2]} for r in rows]
            except Exception:
                return []

    @app.get("/api/agents/{name}/runs")
    async def agent_runs(name: str) -> list[dict]:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return []
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT id, status, started_at, ended_at, error, COALESCE(total_tokens,0), COALESCE(llm_calls,0) FROM agent_runs ORDER BY started_at DESC LIMIT 20")).fetchall()
                return [
                    {
                        "id": r[0][:8],
                        "status": r[1],
                        "started_at": r[2],
                        "ended_at": r[3],
                        "error": r[4],
                        "total_tokens": r[5],
                        "llm_calls": r[6],
                    }
                    for r in rows
                ]
            except Exception:
                return []

    @app.get("/api/agents/{name}/logs")
    async def agent_logs(name: str, lines: int = 100) -> dict:
        import re

        _ansi = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
        log_path = PM.log_file(name)
        if not log_path.exists():
            return {"lines": []}
        content = log_path.read_text(errors="replace")
        tail = [_ansi.sub("", l) for l in content.splitlines()[-lines:]]
        return {"lines": tail}

    @app.get("/api/agents/{name}/timeline")
    async def agent_timeline(name: str, limit: int = 50) -> list[dict]:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return []
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (
                    await db.execute(
                        "SELECT event_type, data, created_at FROM timeline ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    )
                ).fetchall()
                return [{"event": r[0], "data": _preview(r[1], 200), "at": r[2]} for r in rows]
            except Exception:
                return []

    @app.get("/api/agents/{name}/checkpoints")
    async def agent_checkpoints(name: str, run_id: str = "") -> list[dict]:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return []
        async with aiosqlite.connect(db_path) as db:
            try:
                if run_id:
                    rows = await (
                        await db.execute(
                            "SELECT tool_name, args_hash, created_at FROM checkpoints WHERE agent_id = ? AND run_id = ? ORDER BY created_at ASC",
                            (name, run_id),
                        )
                    ).fetchall()
                else:
                    # Latest run
                    run_row = await (
                        await db.execute(
                            "SELECT id FROM agent_runs WHERE agent_id = ? ORDER BY started_at DESC LIMIT 1",
                            (name,),
                        )
                    ).fetchone()
                    if not run_row:
                        return []
                    rows = await (
                        await db.execute(
                            "SELECT tool_name, args_hash, created_at FROM checkpoints WHERE agent_id = ? AND run_id = ? ORDER BY created_at ASC",
                            (name, run_row[0]),
                        )
                    ).fetchall()
                return [{"tool": r[0], "args_hash": r[1], "at": r[2]} for r in rows]
            except Exception:
                return []

    @app.get("/api/agents/{name}/memory/graph")
    async def memory_graph(name: str) -> dict:
        """Return memory keys as a graph (nodes + cluster groups) for canvas rendering."""
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return {"nodes": [], "clusters": []}
        try:
            async with aiosqlite.connect(db_path) as db:
                rows = await (
                    await db.execute(
                        "SELECT key, value, updated_at FROM memory_long WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 200",
                        (name,),
                    )
                ).fetchall()
        except Exception:
            return {"nodes": [], "clusters": []}

        nodes = []
        cluster_map: dict[str, list[int]] = {}
        for i, (key, value, updated_at) in enumerate(rows):
            # Cluster by first segment of colon- or underscore-separated key
            sep = ":" if ":" in key else "_" if "_" in key else None
            cluster = key.split(sep)[0] if sep else "__root__"
            cluster_map.setdefault(cluster, []).append(i)
            nodes.append(
                {
                    "id": i,
                    "key": key,
                    "preview": _preview(value, 60),
                    "updated_at": updated_at or "",
                    "cluster": cluster,
                }
            )

        clusters = [{"name": name_, "node_ids": ids} for name_, ids in cluster_map.items()]
        return {"nodes": nodes, "clusters": clusters}

    # ── Export endpoints ─────────────────────────────────────────────────────

    @app.get("/api/agents/{name}/export/runs.csv")
    async def export_runs_csv(name: str) -> Response:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return Response(content="run_id,status,started_at,ended_at,duration_s,total_tokens,llm_calls,error\n", media_type="text/csv")
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["run_id", "status", "started_at", "ended_at", "duration_s", "total_tokens", "llm_calls", "error"])
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT id, status, started_at, ended_at, COALESCE(total_tokens,0), COALESCE(llm_calls,0), error FROM agent_runs ORDER BY started_at DESC")).fetchall()
                for r in rows:
                    dur = ""
                    try:
                        from datetime import datetime

                        fmt = "%Y-%m-%d %H:%M:%S"
                        s = datetime.strptime(r[2][:19], fmt)
                        e = datetime.strptime(r[3][:19], fmt) if r[3] else s
                        dur = str(int((e - s).total_seconds()))
                    except Exception:
                        pass
                    writer.writerow([r[0], r[1], r[2], r[3] or "", dur, r[4], r[5], r[6] or ""])
            except Exception:
                pass
        return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}-runs.csv"'})

    @app.get("/api/agents/{name}/export/memory.csv")
    async def export_memory_csv(name: str) -> Response:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return Response(content="key,value,updated_at\n", media_type="text/csv")
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["key", "value", "updated_at"])
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT key, value, updated_at FROM memory_long ORDER BY updated_at DESC")).fetchall()
                for r in rows:
                    writer.writerow([r[0], r[1], r[2]])
            except Exception:
                pass
        return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}-memory.csv"'})

    @app.get("/api/agents/{name}/export/timeline.csv")
    async def export_timeline_csv(name: str) -> Response:
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return Response(content="event_type,data,created_at\n", media_type="text/csv")
        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["event_type", "data", "created_at"])
        async with aiosqlite.connect(db_path) as db:
            try:
                rows = await (await db.execute("SELECT event_type, data, created_at FROM memory_timeline ORDER BY created_at DESC")).fetchall()
                for r in rows:
                    writer.writerow([r[0], r[1], r[2]])
            except Exception:
                # try old column name
                try:
                    rows = await (await db.execute("SELECT event, data, created_at FROM memory_timeline ORDER BY created_at DESC")).fetchall()
                    for r in rows:
                        writer.writerow([r[0], r[1], r[2]])
                except Exception:
                    pass
        return Response(content=output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}-timeline.csv"'})

    @app.get("/api/agents/{name}/export/report.json")
    async def export_report_json(name: str) -> Response:
        """Full agent report as JSON — runs + memory + timeline."""
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        report: dict = {"agent": name, "generated_at": None, "runs": [], "memory": {}, "timeline": []}
        from datetime import datetime, timezone

        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        if db_path.exists():
            async with aiosqlite.connect(db_path) as db:
                try:
                    rows = await (await db.execute("SELECT id, status, started_at, ended_at, COALESCE(total_tokens,0), COALESCE(llm_calls,0), error FROM agent_runs ORDER BY started_at DESC")).fetchall()
                    report["runs"] = [{"id": r[0], "status": r[1], "started_at": r[2], "ended_at": r[3], "total_tokens": r[4], "llm_calls": r[5], "error": r[6]} for r in rows]
                except Exception:
                    pass
                try:
                    rows = await (await db.execute("SELECT key, value FROM memory_long ORDER BY updated_at DESC")).fetchall()
                    for key, val in rows:
                        try:
                            report["memory"][key] = json.loads(val)
                        except Exception:
                            report["memory"][key] = val
                except Exception:
                    pass
                try:
                    rows = await (await db.execute("SELECT event_type, data, created_at FROM memory_timeline ORDER BY created_at DESC LIMIT 200")).fetchall()
                    for r in rows:
                        try:
                            report["timeline"].append({"event": r[0], "data": json.loads(r[1] or "{}"), "at": r[2]})
                        except Exception:
                            report["timeline"].append({"event": r[0], "data": r[1], "at": r[2]})
                except Exception:
                    pass
        return Response(
            content=json.dumps(report, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{name}-report.json"'},
        )

    # ── Workflow / Visual Builder endpoints ──────────────────────────────────

    @app.get("/api/workflow/{name}")
    async def get_workflow(name: str) -> dict:
        """Load a saved workflow graph."""
        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        if not db_path.exists():
            return {"nodes": [], "edges": []}
        async with aiosqlite.connect(db_path) as db:
            try:
                await db.execute("CREATE TABLE IF NOT EXISTS workflows (name TEXT PRIMARY KEY, graph TEXT, updated_at TEXT)")
                row = await (await db.execute("SELECT graph FROM workflows WHERE name = ?", (name,))).fetchone()
                return json.loads(row[0]) if row else {"nodes": [], "edges": []}
            except Exception:
                return {"nodes": [], "edges": []}

    @app.post("/api/workflow/{name}")
    async def save_workflow(name: str, body: dict) -> dict:
        """Save a workflow graph."""
        from datetime import datetime, timezone

        db_path = PM.AIOS_DIR / "data" / f"{name}.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("CREATE TABLE IF NOT EXISTS workflows (name TEXT PRIMARY KEY, graph TEXT, updated_at TEXT)")
            await db.execute(
                "INSERT INTO workflows (name, graph, updated_at) VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET graph=excluded.graph, updated_at=excluded.updated_at",
                (name, json.dumps(body), datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        return {"ok": True}

    @app.post("/api/workflow/{name}/export_python")
    async def export_workflow_python(name: str, body: dict) -> dict:
        """Convert a workflow graph to a Python agent class."""
        nodes = body.get("nodes", [])
        edges = body.get("edges", [])
        code = _workflow_to_python(name, nodes, edges)
        return {"code": code}

    # ── WebSocket log streaming (for remote/cloud hosted agents) ─────────────

    @app.websocket("/ws/agents/{name}/logs")
    async def ws_log_stream(websocket: WebSocket, name: str) -> None:
        """WebSocket endpoint — streams log lines to remote clients.

        Connect with any WS client:
          wscat -c ws://your-host:8000/ws/agents/myagent/logs

        Each message is a plain text log line (ANSI stripped).
        """
        import re

        _ansi = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
        await websocket.accept()
        log_path = PM.log_file(name)
        try:
            if not log_path.exists():
                await websocket.send_text(f"[aios] No logs yet for '{name}'")
            with log_path.open("r", errors="replace") as f:
                f.seek(0, 2)  # tail mode — seek to end
                while True:
                    line = f.readline()
                    if line:
                        clean = _ansi.sub("", line.rstrip())
                        await websocket.send_text(clean)
                    else:
                        await asyncio.sleep(0.2)
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await websocket.close()
            except Exception:
                pass

    @app.get("/api/agents/{name}/logs/stream")
    async def stream_logs(name: str) -> StreamingResponse:
        import re

        _ansi = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
        log_path = PM.log_file(name)

        async def generator():
            if not log_path.exists():
                yield 'data: {"line": "No logs yet"}\n\n'
                return
            with log_path.open("r", errors="replace") as f:
                f.seek(0, 2)  # seek to end
                while True:
                    line = f.readline()
                    if line:
                        clean = _ansi.sub("", line.rstrip())
                        yield f"data: {json.dumps({'line': clean})}\n\n"
                    else:
                        await asyncio.sleep(0.2)

        return StreamingResponse(generator(), media_type="text/event-stream")

    return app


async def _count_memory(db_path: Path) -> int:
    try:
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute("SELECT COUNT(*) FROM memory_long")).fetchone()
            return row[0] if row else 0
    except Exception:
        return 0


async def _last_run(db_path: Path) -> str | None:
    try:
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute("SELECT started_at, status FROM agent_runs ORDER BY started_at DESC LIMIT 1")).fetchone()
            return f"{row[0][:19]} ({row[1]})" if row else None
    except Exception:
        return None


def _workflow_to_python(workflow_name: str, nodes: list, edges: list) -> str:
    """Convert a visual workflow graph into a runnable Python agent class."""
    class_name = "".join(w.capitalize() for w in workflow_name.replace("-", "_").split("_")) + "Agent"
    tools_code: list[str] = []
    run_steps: list[str] = []

    # Build tool methods from tool nodes
    for node in nodes:
        ntype = node.get("type", "")
        label = node.get("label", node.get("id", "step"))
        safe_name = label.lower().replace(" ", "_").replace("-", "_")[:30]

        if ntype == "tool":
            prompt = node.get("prompt", f"Execute: {label}")
            tools_code.append(
                f"    @tool\n"
                f"    async def {safe_name}(self, input: str) -> str:\n"
                f'        """{label}. input: The input to process."""\n'
                f"        # TODO: implement {label}\n"
                f'        return await self.think("{prompt}: {{input}}")\n'
            )
        elif ntype == "llm":
            prompt = node.get("prompt", f"Process: {label}")
            run_steps.append(f'        # {label}\n        {safe_name}_result = await self.think("{prompt}")\n        await self.memory.save("{safe_name}", {safe_name}_result)\n')
        elif ntype == "memory_read":
            key = node.get("key", safe_name)
            run_steps.append(f'        {safe_name} = await self.memory.load("{key}")\n')
        elif ntype == "memory_write":
            key = node.get("key", safe_name)
            run_steps.append(f'        await self.memory.save("{key}", {safe_name}_result)\n')
        elif ntype == "start":
            pass  # implicit
        elif ntype == "end":
            run_steps.append("        # Workflow complete\n")

    tool_methods = "\n".join(tools_code) if tools_code else ""
    run_body = "".join(run_steps) if run_steps else "        pass\n"

    return (
        f"from aios import Agent, tool\n\n\n"
        f"class {class_name}(Agent):\n"
        f'    name = "{workflow_name}"\n'
        f'    model = "claude-sonnet-4-6"\n'
        f'    description = "Generated from visual workflow: {workflow_name}"\n'
        f'    system_prompt = "You are a helpful AI agent."\n\n'
        f"{tool_methods}"
        f"    async def run(self) -> None:\n"
        f"{run_body}\n\n"
        f'if __name__ == "__main__":\n'
        f"    {class_name}.launch()\n"
    )


def _preview(raw: str, length: int = 120) -> str:
    try:
        val = json.loads(raw)
        s = json.dumps(val)
    except Exception:
        s = raw
    return s[:length] + ("…" if len(s) > length else "")


_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ai.os — Agent Runtime</title>
<link rel="icon" href="/favicon.ico" type="image/svg+xml">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}

/* ── Dark theme (default) ── */
:root,[data-theme="dark"]{
  --bg:#07080E;--s1:#0C0E18;--s2:#101320;--s3:#141729;
  --border:#181B2E;--border2:#1F2340;--border3:#252A4A;
  --blue:#5B8DF6;--blue-dim:rgba(91,141,246,.12);--blue-glow:rgba(91,141,246,.25);
  --green:#2DD67B;--green-dim:rgba(45,214,123,.1);
  --red:#E05C5C;--red-dim:rgba(224,92,92,.1);
  --amber:#F0A843;--amber-dim:rgba(240,168,67,.1);
  --text:#DDE4F5;--sub:#7B82A3;--muted:#3E4360;
  --log-bg:#0C0E18;--log-border:#181B2E;
  --radius:8px;--radius-lg:12px;
  --shadow:0 1px 3px rgba(0,0,0,.5);
}

/* ── Light theme ── */
[data-theme="light"]{
  --bg:#F4F6FB;--s1:#FFFFFF;--s2:#EEF1F8;--s3:#E5E9F4;
  --border:#DDE2EF;--border2:#CDD4E8;--border3:#B8C3DC;
  --blue:#3B6FE8;--blue-dim:rgba(59,111,232,.08);--blue-glow:rgba(59,111,232,.2);
  --green:#179B55;--green-dim:rgba(23,155,85,.08);
  --red:#C94040;--red-dim:rgba(201,64,64,.08);
  --amber:#C07C10;--amber-dim:rgba(192,124,16,.08);
  --text:#1A1F35;--sub:#5A6480;--muted:#A0AAC4;
  --log-bg:#FAFBFF;--log-border:#DDE2EF;
  --shadow:0 1px 3px rgba(0,0,0,.08);
}

html,body{height:100%;overflow:hidden}
body{
  background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',system-ui,sans-serif;
  font-size:13px;line-height:1.5;
  transition:background .2s,color .2s;
}

/* ── Header ── */
header{
  height:52px;padding:0 20px;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:0;
  background:var(--s1);
  position:relative;z-index:10;
  box-shadow:var(--shadow);
}
.logo{font-size:15px;font-weight:800;letter-spacing:-.04em;color:var(--text);user-select:none}
.logo-dot{color:var(--blue)}
.header-tag{
  font-size:10px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);margin-left:12px;padding-left:12px;
  border-left:1px solid var(--border2);
}
.header-stats{display:flex;gap:16px;margin-left:auto;align-items:center}
.hstat{font-size:12px;color:var(--sub)}
.hstat strong{color:var(--text);font-weight:700}
.btn-icon{
  background:transparent;border:1px solid var(--border2);border-radius:6px;
  color:var(--sub);cursor:pointer;padding:5px 10px;font-size:12px;
  transition:all .15s;display:flex;align-items:center;gap:5px;margin-left:8px;
}
.btn-icon:hover{border-color:var(--border3);color:var(--text);background:var(--s2)}
.btn-icon.spinning svg{animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.theme-btn{padding:5px 9px;font-size:14px;line-height:1}
.export-wrap{position:relative;margin-left:auto}
.export-dropdown{
  position:absolute;top:calc(100% + 6px);right:0;z-index:100;
  background:var(--s1);border:1px solid var(--border2);border-radius:8px;
  padding:6px;min-width:150px;
  box-shadow:0 8px 24px rgba(0,0,0,.35);
  display:none;flex-direction:column;gap:2px;
}
.export-dropdown.open{display:flex}
.export-item{
  padding:7px 12px;font-size:12px;font-weight:500;color:var(--text);
  border-radius:5px;cursor:pointer;white-space:nowrap;
  transition:background .1s;
}
.export-item:hover{background:var(--s2);color:var(--blue)}
.export-sep{height:1px;background:var(--border);margin:3px 0}
@media print{
  .sidebar,.header-stats,.export-wrap,.btn-icon,.theme-btn,header{display:none!important}
  .panel{width:100%;margin:0;border:none}
  body{overflow:auto}
  .tab-content.active{display:block!important}
}

/* ── Layout ── */
main{display:grid;grid-template-columns:236px 1fr;height:calc(100vh - 52px);overflow:hidden}

/* ── Sidebar ── */
.sidebar{
  border-right:1px solid var(--border);
  overflow-y:auto;display:flex;flex-direction:column;
  background:var(--s1);
}
.sidebar::-webkit-scrollbar{width:4px}
.sidebar::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
.sidebar-header{padding:14px 16px 8px;display:flex;align-items:center;justify-content:space-between}
.sidebar-label{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.agent-count{
  font-size:11px;font-weight:700;color:var(--sub);
  background:var(--s2);border:1px solid var(--border);
  border-radius:10px;padding:1px 7px;
}
.agent-row{
  display:flex;align-items:center;gap:10px;
  padding:9px 16px;cursor:pointer;
  border-left:2px solid transparent;
  transition:background .12s,border-color .12s;
}
.agent-row:hover{background:var(--s2)}
.agent-row.active{background:var(--blue-dim);border-left-color:var(--blue)}
.dot-wrap{position:relative;flex-shrink:0}
.dot{width:7px;height:7px;border-radius:50%;display:block}
.dot-green{background:var(--green);animation:pulse-green 2.5s infinite}
.dot-muted{background:var(--muted)}
@keyframes pulse-green{
  0%{box-shadow:0 0 0 0 rgba(45,214,123,.5)}
  70%{box-shadow:0 0 0 5px rgba(45,214,123,0)}
  100%{box-shadow:0 0 0 0 rgba(45,214,123,0)}
}
.agent-info{min-width:0;flex:1}
.agent-name{font-weight:600;font-size:13px;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.agent-meta{font-size:11px;color:var(--muted);margin-top:1px}
.no-agents{padding:24px 16px;color:var(--muted);font-size:12px;text-align:center;line-height:1.8}
.no-agents code{background:var(--s2);padding:1px 5px;border-radius:4px;font-size:11px}

/* ── Panel ── */
.panel{overflow-y:auto;display:flex;flex-direction:column;background:var(--bg)}
.panel::-webkit-scrollbar{width:5px}
.panel::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
.panel-inner{padding:22px 28px;flex:1}
.empty-state{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;gap:14px;color:var(--muted);text-align:center;
}
.empty-state-icon{font-size:36px;opacity:.25}
.empty-state-text{font-size:13px;line-height:1.8;color:var(--muted)}

/* ── Panel header ── */
.panel-header{display:flex;align-items:center;gap:10px;margin-bottom:18px;flex-wrap:wrap}
.panel-title{font-size:17px;font-weight:800;color:var(--text);letter-spacing:-.03em}
.panel-subtitle{font-size:11px;color:var(--muted);margin-left:2px;font-family:'Courier New',monospace}
.badge{
  font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  padding:2px 8px;border-radius:20px;white-space:nowrap;
}
.badge-green{background:var(--green-dim);color:var(--green);border:1px solid rgba(45,214,123,.25)}
.badge-running{background:var(--green-dim);color:var(--green);border:1px solid rgba(45,214,123,.25);animation:badge-pulse 2s infinite}
@keyframes badge-pulse{0%,100%{opacity:1}50%{opacity:.65}}
.badge-red{background:var(--red-dim);color:var(--red);border:1px solid rgba(224,92,92,.25)}
.badge-amber{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(240,168,67,.25)}
.badge-muted{background:var(--s2);color:var(--muted);border:1px solid var(--border)}
.badge-blue{background:var(--blue-dim);color:var(--blue);border:1px solid rgba(91,141,246,.25)}

/* ── Stats ── */
.stat-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:20px}
.stat{
  background:var(--s1);border:1px solid var(--border);
  border-radius:var(--radius);padding:14px 16px;
  transition:border-color .15s;
}
.stat:hover{border-color:var(--border2)}
.stat-num{font-size:22px;font-weight:800;color:var(--text);letter-spacing:-.05em;line-height:1.1;font-variant-numeric:tabular-nums}
.stat-label{font-size:10px;color:var(--muted);margin-top:5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase}

/* ── Tabs ── */
.tabs{display:flex;border-bottom:1px solid var(--border);margin-bottom:16px;gap:0}
.tab{
  padding:7px 13px;font-size:12px;font-weight:600;cursor:pointer;
  color:var(--muted);border-bottom:2px solid transparent;
  margin-bottom:-1px;transition:color .15s,border-color .15s;
  letter-spacing:.02em;white-space:nowrap;
}
.tab.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab:hover:not(.active){color:var(--sub)}
.tab-count{
  display:inline-block;margin-left:4px;
  font-size:10px;font-weight:700;color:var(--muted);
  background:var(--s2);border-radius:8px;padding:0 5px;
}
.tab-content{display:none}.tab-content.active{display:block}

/* ── Log box ── */
.log-toolbar{display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap}
.log-search{
  flex:1;min-width:140px;background:var(--s1);border:1px solid var(--border);
  border-radius:6px;padding:5px 10px;color:var(--text);font-size:12px;
  font-family:inherit;outline:none;transition:border-color .15s;
}
.log-search:focus{border-color:var(--blue)}
.log-search::placeholder{color:var(--muted)}
.log-toggle{
  font-size:11px;font-weight:600;color:var(--sub);cursor:pointer;
  background:var(--s1);border:1px solid var(--border);border-radius:6px;
  padding:4px 10px;transition:all .15s;white-space:nowrap;
}
.log-toggle:hover{color:var(--text);border-color:var(--border2)}
.log-toggle.active{color:var(--blue);border-color:var(--blue);background:var(--blue-dim)}
.live-badge{
  font-size:10px;font-weight:700;letter-spacing:.06em;padding:3px 8px;
  border-radius:20px;background:var(--green-dim);color:var(--green);
  border:1px solid rgba(45,214,123,.25);display:flex;align-items:center;gap:5px;
}
.live-dot{width:5px;height:5px;border-radius:50%;background:var(--green);animation:pulse-green 1.5s infinite}
.log-box{
  background:var(--log-bg);border:1px solid var(--log-border);
  border-radius:var(--radius);padding:12px 16px;
  height:calc(100vh - 400px);min-height:180px;
  overflow-y:auto;font-family:'Courier New',Menlo,'Fira Code',monospace;
  font-size:11.5px;line-height:1.8;
}
.log-box::-webkit-scrollbar{width:5px}
.log-box::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
.log-line{padding:0;white-space:pre-wrap;word-break:break-all}
.log-line.ll-info{color:var(--sub)}
.log-line.ll-debug{color:var(--muted)}
.log-line.ll-warn{color:var(--amber)}
.log-line.ll-error{color:var(--red)}
.log-line.ll-success{color:var(--green)}
.log-line.ll-highlight{color:var(--text);font-weight:600}
.log-line.ll-noise{color:var(--muted);opacity:.45}
.log-line.ll-stream{color:var(--blue);opacity:.8;font-style:italic;letter-spacing:.01em}
.log-line.hidden,.log-line.noise-hidden{display:none}

/* ── Table ── */
.table-wrap{overflow-x:auto;border-radius:var(--radius);border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;font-size:12px}
th{
  text-align:left;font-size:10px;font-weight:700;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);
  padding:8px 14px 9px;border-bottom:1px solid var(--border);
  background:var(--s1);
}
td{
  padding:10px 14px;border-bottom:1px solid var(--border);
  color:var(--text);font-variant-numeric:tabular-nums;
}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--s2)}
.mono{font-family:'Courier New',Menlo,monospace;font-size:11px;color:var(--sub)}
.cell-muted{color:var(--sub)}
.cell-dim{color:var(--muted)}
.time-cell{white-space:nowrap}
.time-abs{font-size:11px;color:var(--muted);display:block;margin-top:2px}

/* ── Timeline ── */
.timeline-feed{padding:4px 0}
.tl-item{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--border)}
.tl-item:last-child{border-bottom:none}
.tl-line{display:flex;flex-direction:column;align-items:center;flex-shrink:0}
.tl-dot{width:8px;height:8px;border-radius:50%;background:var(--blue);box-shadow:0 0 0 3px var(--blue-dim);margin-top:3px}
.tl-body{flex:1;min-width:0}
.tl-header{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tl-time{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
.tl-data{font-size:11px;color:var(--sub);font-family:'Courier New',Menlo,monospace;margin-top:6px;word-break:break-all;white-space:pre-wrap;max-height:80px;overflow:hidden;background:var(--s2);border-radius:5px;padding:5px 8px}

/* ── Trace tab note ── */
.trace-note{
  margin-top:12px;padding:9px 12px;border-radius:var(--radius);
  background:var(--blue-dim);border:1px solid rgba(91,141,246,.2);
  font-size:11px;color:var(--sub);line-height:1.6;
}

/* ── Misc ── */
.empty{color:var(--muted);font-size:12px;padding:40px 0;text-align:center;line-height:2.2}
.empty code{background:var(--s2);padding:1px 6px;border-radius:4px;font-size:11px}
.memory-value{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.divider{height:1px;background:var(--border);margin:18px 0}
</style>
</head>
<body>
<header>
  <div class="logo">Ai<span class="logo-dot">.</span>os</div>
  <span class="header-tag">Runtime</span>
  <div class="header-stats">
    <span class="hstat" id="h-running">— running</span>
    <span class="hstat" id="h-total">— agents</span>
  </div>
  <button class="btn-icon" id="refresh-btn" onclick="manualRefresh()" title="Refresh">
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
      <path d="M13.5 2.5A7 7 0 1 0 15 8"/>
      <polyline points="11 1 15 1 15 5"/>
    </svg>
    Refresh
  </button>
  <button class="btn-icon theme-btn" id="theme-btn" onclick="toggleTheme()" title="Toggle theme">🌙</button>
</header>
<main>
  <div class="sidebar">
    <div class="sidebar-header">
      <span class="sidebar-label">Agents</span>
      <span class="agent-count" id="agent-count"></span>
    </div>
    <div id="agent-list"></div>
  </div>
  <div class="panel" id="main-panel">
    <div class="empty-state">
      <div class="empty-state-icon">◎</div>
      <div class="empty-state-text">Select an agent from the sidebar<br>to inspect its state and logs.</div>
    </div>
  </div>
</main>

<script>
let agents = [];
let selected = null;
let logEs = null;
let activeTab = 'logs';
let logFilter = '';
let autoScroll = true;
let hideNoise = true;

// ── Theme ──────────────────────────────────────────────

function initTheme() {
  const saved = localStorage.getItem('aios-theme') || 'dark';
  setTheme(saved);
}
function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('theme-btn').textContent = t === 'dark' ? '☀️' : '🌙';
  localStorage.setItem('aios-theme', t);
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute('data-theme');
  setTheme(cur === 'dark' ? 'light' : 'dark');
}

// ── Data fetching ──────────────────────────────────────

async function loadAgents(quiet = false) {
  try {
    const res = await fetch('/api/agents');
    agents = await res.json();
  } catch(e) {
    if (!quiet) console.warn('Failed to load agents', e);
    return;
  }
  renderSidebar();
  updateHeaderStats();
  if (selected) refreshPanel();
}

function updateHeaderStats() {
  const running = agents.filter(a => a.running).length;
  document.getElementById('h-running').innerHTML = `<strong>${running}</strong> running`;
  document.getElementById('h-total').innerHTML = `<strong>${agents.length}</strong> agents`;
  const c = document.getElementById('agent-count');
  if (c) c.textContent = agents.length || '';
}

async function manualRefresh() {
  const btn = document.getElementById('refresh-btn');
  btn.classList.add('spinning');
  await loadAgents();
  if (selected) await loadPanel(selected);
  setTimeout(() => btn.classList.remove('spinning'), 400);
}

// ── Sidebar ────────────────────────────────────────────

function renderSidebar() {
  const el = document.getElementById('agent-list');
  if (!agents.length) {
    el.innerHTML = `<div class="no-agents">No agents found.<br>Run <code>aios run agent.py -d</code><br>to start one.</div>`;
    return;
  }
  el.innerHTML = agents.map(a => `
    <div class="agent-row ${selected === a.name ? 'active' : ''}" onclick="selectAgent('${esc(a.name)}')">
      <div class="dot-wrap"><span class="dot ${a.running ? 'dot-green' : 'dot-muted'}"></span></div>
      <div class="agent-info">
        <div class="agent-name">${escHtml(a.name)}</div>
        <div class="agent-meta">${a.running ? 'running' : 'stopped'}${a.pid ? ' · pid ' + a.pid : ''}</div>
      </div>
    </div>
  `).join('');
}

// ── Panel ──────────────────────────────────────────────

async function selectAgent(name) {
  selected = name;
  activeTab = 'logs';
  renderSidebar();
  await loadPanel(name);
}

async function refreshPanel() {
  if (!selected) return;
  const lb = document.getElementById('log-box');
  const wasAtBottom = lb ? lb.scrollHeight - lb.scrollTop - lb.clientHeight < 50 : true;
  autoScroll = wasAtBottom;
  await loadPanel(selected, true);
}

async function loadPanel(name, preserveScroll = false) {
  const agent = agents.find(a => a.name === name) || {name, running: false};

  const [memory, runs, logs, timeline, checkpoints] = await Promise.all([
    fetch(`/api/agents/${name}/memory`).then(r => r.json()).catch(() => []),
    fetch(`/api/agents/${name}/runs`).then(r => r.json()).catch(() => []),
    fetch(`/api/agents/${name}/logs?lines=400`).then(r => r.json()).catch(() => ({lines:[]})),
    fetch(`/api/agents/${name}/timeline`).then(r => r.json()).catch(() => []),
    fetch(`/api/agents/${name}/checkpoints`).then(r => r.json()).catch(() => []),
  ]);

  const running = agent.running;
  const completed = runs.filter(r => r.status === 'completed').length;
  const failed = runs.filter(r => r.status === 'failed').length;

  // ── Logs ──
  const logHtml = logs.lines.length
    ? logs.lines.map(l => {
        const cls = logClass(l);
        const noisy = isNoise(l);
        return `<div class="log-line ${cls}${noisy ? ' ll-noise' : ''}${noisy && hideNoise ? ' noise-hidden' : ''}">${escHtml(l)}</div>`;
      }).join('')
    : '<div class="empty">No logs yet.</div>';

  // ── Memory ──
  const memHtml = memory.length
    ? `<div class="table-wrap"><table>
        <thead><tr><th>Key</th><th>Value</th><th>Updated</th></tr></thead>
        <tbody>
        ${memory.map(m => `<tr>
          <td class="mono">${escHtml(m.key)}</td>
          <td class="mono memory-value" title="${escHtml(m.value)}">${escHtml(m.value)}</td>
          <td class="time-cell cell-dim">${fmtTimeCell(m.updated_at)}</td>
        </tr>`).join('')}
        </tbody></table></div>`
    : '<div class="empty">No memory stored yet.<br>Use <code>await self.memory.save("key", value)</code></div>';

  // ── Timeline ──
  const timelineHtml = timeline.length
    ? `<div class="timeline-feed">
        ${timeline.map(e => `
          <div class="tl-item">
            <div class="tl-line"><div class="tl-dot"></div></div>
            <div class="tl-body">
              <div class="tl-header">
                <span class="badge badge-blue">${escHtml(e.event)}</span>
                <span class="tl-time">${fmtRelative(e.at)}</span>
              </div>
              <div class="tl-data">${escHtml(e.data)}</div>
            </div>
          </div>`).join('')}
      </div>`
    : '<div class="empty">No timeline events yet.<br>Use <code>await self.memory.log_event("name", data)</code></div>';

  // ── Trace ──
  const traceHtml = checkpoints.length
    ? `<div class="table-wrap"><table>
        <thead><tr><th>Tool called</th><th>Args hash</th><th>Cached</th></tr></thead>
        <tbody>
        ${checkpoints.map(c => `<tr>
          <td style="color:var(--blue);font-weight:600">${escHtml(c.tool)}</td>
          <td class="mono cell-dim">${escHtml(c.args_hash)}</td>
          <td class="time-cell">${fmtTimeCell(c.at)}</td>
        </tr>`).join('')}
        </tbody></table></div>
        <div class="trace-note">
          ⚡ ${checkpoints.length} tool call${checkpoints.length===1?'':'s'} cached from the latest run.
          On crash recovery these replay <strong>instantly</strong> — the agent fast-forwards to the first un-cached call.
        </div>`
    : '<div class="empty">No tool calls cached yet.<br>Results appear here after the first run completes.</div>';

  // ── History ──
  const totalTokens = runs.reduce((s, r) => s + (r.total_tokens || 0), 0);
  const runsHtml = runs.length
    ? `<div class="table-wrap"><table>
        <thead><tr><th>Run ID</th><th>Status</th><th>Started</th><th>Duration</th><th>Tokens</th><th>LLM calls</th><th>Error</th></tr></thead>
        <tbody>
        ${runs.map(r => {
          const dur = r.started_at && r.ended_at
            ? fmtDuration(r.started_at, r.ended_at)
            : r.status === 'running' ? '<span style="color:var(--green)">running…</span>' : '—';
          const badgeCls = r.status === 'completed' ? 'badge-green' : r.status === 'running' ? 'badge-running' : 'badge-red';
          const tok = r.total_tokens ? fmtTokens(r.total_tokens) : '<span style="color:var(--muted)">—</span>';
          const calls = r.llm_calls || '<span style="color:var(--muted)">—</span>';
          return `<tr>
            <td class="mono">${escHtml(r.id)}</td>
            <td><span class="badge ${badgeCls}">${escHtml(r.status)}</span></td>
            <td class="time-cell">${fmtTimeCell(r.started_at)}</td>
            <td class="cell-dim" style="font-variant-numeric:tabular-nums">${dur}</td>
            <td class="cell-muted" style="font-variant-numeric:tabular-nums">${tok}</td>
            <td class="cell-dim" style="text-align:center">${calls}</td>
            <td class="mono" style="color:var(--red);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${r.error ? escHtml(r.error.slice(0,60)) : ''}</td>
          </tr>`;
        }).join('')}
        </tbody></table></div>
        ${totalTokens ? `<div style="padding:8px 2px 0;font-size:11px;color:var(--muted)">${fmtTokens(totalTokens)} total tokens across ${runs.length} runs</div>` : ''}`
    : '<div class="empty">No runs recorded yet.</div>';

  // ── Assemble ──
  document.getElementById('main-panel').innerHTML = `
    <div class="panel-inner">
      <div class="panel-header">
        <div class="panel-title">${escHtml(name)}</div>
        <span class="badge ${running ? 'badge-running' : 'badge-muted'}">${running ? '● running' : 'stopped'}</span>
        ${agent.file ? `<span class="panel-subtitle">${escHtml(agent.file.split(/[\\/]/).pop())}</span>` : ''}
        <div class="export-wrap">
          <button class="btn-icon" onclick="toggleExportMenu(event)" title="Export data">
            <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1v10M4 7l4 4 4-4"/><path d="M2 14h12"/></svg>
            Export
          </button>
          <div class="export-dropdown" id="export-menu">
            <div class="export-item" onclick="exportCSV('runs');closeExportMenu()">Runs CSV</div>
            <div class="export-item" onclick="exportCSV('memory');closeExportMenu()">Memory CSV</div>
            <div class="export-item" onclick="exportCSV('timeline');closeExportMenu()">Timeline CSV</div>
            <div class="export-sep"></div>
            <div class="export-item" onclick="exportJSON();closeExportMenu()">Full JSON report</div>
            <div class="export-item" onclick="window.print();closeExportMenu()">Save as PDF</div>
          </div>
        </div>
      </div>

      <div class="stat-row">
        <div class="stat">
          <div class="stat-num">${runs.length}</div>
          <div class="stat-label">Total runs</div>
        </div>
        <div class="stat">
          <div class="stat-num" style="color:var(--green)">${completed}</div>
          <div class="stat-label">Completed</div>
        </div>
        <div class="stat">
          <div class="stat-num" style="color:${failed ? 'var(--red)' : 'var(--muted)'}">${failed}</div>
          <div class="stat-label">Failed</div>
        </div>
        <div class="stat">
          <div class="stat-num">${memory.length}</div>
          <div class="stat-label">Memory keys</div>
        </div>
      </div>

      <div class="tabs">
        <div class="tab ${activeTab==='logs'?'active':''}" onclick="switchTab('logs')">Logs</div>
        <div class="tab ${activeTab==='memory'?'active':''}" onclick="switchTab('memory')">Memory<span class="tab-count">${memory.length}</span></div>
        <div class="tab ${activeTab==='runs'?'active':''}" onclick="switchTab('runs')">History<span class="tab-count">${runs.length}</span></div>
        <div class="tab ${activeTab==='timeline'?'active':''}" onclick="switchTab('timeline')">Timeline<span class="tab-count">${timeline.length}</span></div>
        <div class="tab ${activeTab==='trace'?'active':''}" onclick="switchTab('trace')">Trace<span class="tab-count">${checkpoints.length}</span></div>
        <div class="tab ${activeTab==='graph'?'active':''}" onclick="switchTab('graph');mgLoad()">Graph</div>
      </div>

      <div id="tab-logs" class="tab-content ${activeTab==='logs'?'active':''}">
        <div class="log-toolbar">
          <input class="log-search" type="text" placeholder="Filter logs…" value="${escHtml(logFilter)}" oninput="filterLogs(this.value)">
          <button class="log-toggle ${hideNoise?'active':''}" onclick="toggleNoise()" title="Hide LiteLLM and framework noise">Hide noise</button>
          ${running ? '<span class="live-badge"><span class="live-dot"></span>Live</span>' : ''}
        </div>
        <div class="log-box" id="log-box" onscroll="onLogScroll()">${logHtml}</div>
      </div>
      <div id="tab-memory" class="tab-content ${activeTab==='memory'?'active':''}">${memHtml}</div>
      <div id="tab-runs" class="tab-content ${activeTab==='runs'?'active':''}">${runsHtml}</div>
      <div id="tab-timeline" class="tab-content ${activeTab==='timeline'?'active':''}">${timelineHtml}</div>
      <div id="tab-trace" class="tab-content ${activeTab==='trace'?'active':''}">${traceHtml}</div>
    </div>
  `;

  const lb = document.getElementById('log-box');
  if (lb && (!preserveScroll || autoScroll)) lb.scrollTop = lb.scrollHeight;
  if (logFilter) filterLogs(logFilter);
  if (!preserveScroll) startLogStream(name, running);
}

// ── Log streaming ──────────────────────────────────────

function startLogStream(name, running) {
  if (logEs) { logEs.close(); logEs = null; }
  if (!running) return;
  logEs = new EventSource(`/api/agents/${name}/logs/stream`);
  logEs.onmessage = e => {
    const data = JSON.parse(e.data);
    const lb = document.getElementById('log-box');
    if (!lb) return;
    const div = document.createElement('div');
    const noisy = isNoise(data.line);
    div.className = 'log-line ' + logClass(data.line) + (noisy ? ' ll-noise' : '') + (noisy && hideNoise ? ' noise-hidden' : '');
    if (logFilter && !data.line.toLowerCase().includes(logFilter.toLowerCase())) div.classList.add('hidden');
    div.textContent = data.line;
    lb.appendChild(div);
    if (autoScroll) lb.scrollTop = lb.scrollHeight;
  };
}

function onLogScroll() {
  const lb = document.getElementById('log-box');
  if (!lb) return;
  autoScroll = lb.scrollHeight - lb.scrollTop - lb.clientHeight < 50;
}

// ── Log coloring ───────────────────────────────────────

function isNoise(line) {
  return /LiteLLM|utils\.py:\d+|provider\s*=\s*\w+|litellm\.py|\[stream\]/i.test(line);
}

function logClass(line) {
  const l = line.toLowerCase();
  if (/\[stream\]/.test(line)) return 'll-stream';
  if (/\b(error|exception|traceback|failed|fail|critical)\b/.test(l)) return 'll-error';
  if (/\b(warning|warn)\b/.test(l)) return 'll-warn';
  if (/\b(debug)\b/.test(l)) return 'll-debug';
  if (/(\brun started\b|\brun complete\b|\bcomplete\b|\bsuccess\b|\bdone\b|\bfinished\b|\bsaved\b|\b✓\b|\b✔\b)/.test(l)) return 'll-success';
  if (/\binfo\b/.test(l)) return 'll-info';
  if (/^\[.*?\]/.test(line) || /^=+/.test(line)) return 'll-highlight';
  return 'll-info';
}

// ── Log filter ─────────────────────────────────────────

function filterLogs(val) {
  logFilter = val;
  const q = val.toLowerCase();
  document.querySelectorAll('.log-line').forEach(el => {
    const noisy = el.classList.contains('ll-noise') && hideNoise;
    const filtered = q.length > 0 && !el.textContent.toLowerCase().includes(q);
    el.classList.toggle('hidden', filtered);
    el.classList.toggle('noise-hidden', noisy && !filtered);
  });
}

function toggleNoise() {
  hideNoise = !hideNoise;
  document.querySelectorAll('.log-line.ll-noise').forEach(el => {
    el.classList.toggle('noise-hidden', hideNoise);
  });
  const btn = document.querySelector('.log-toggle');
  if (btn) btn.classList.toggle('active', hideNoise);
}

// ── Tabs ───────────────────────────────────────────────

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', ['logs','memory','runs','timeline','trace'][i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  const tc = document.getElementById(`tab-${name}`);
  if (tc) tc.classList.add('active');
}

// ── Time utilities ─────────────────────────────────────

function fmtRelative(iso) {
  if (!iso) return '—';
  const d = new Date(iso.replace(' ','T') + (iso.includes('Z') ? '' : 'Z'));
  if (isNaN(d)) return iso.slice(0,19);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 5) return 'just now';
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff/60) + 'm ago';
  if (diff < 86400) return Math.round(diff/3600) + 'h ago';
  if (diff < 7 * 86400) return Math.round(diff/86400) + 'd ago';
  return fmtDate(d);
}

function fmtDate(d) {
  const pad = n => String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fmtTimeCell(iso) {
  if (!iso) return '<span class="cell-dim">—</span>';
  const rel = fmtRelative(iso);
  const abs = iso.slice(0,19).replace('T',' ');
  if (rel === abs || rel.startsWith('20')) {
    return `<span class="time-cell cell-muted">${rel}</span>`;
  }
  return `<span class="time-cell cell-muted" title="${abs}">${rel}</span><span class="time-abs">${abs}</span>`;
}

function fmtTokens(n) {
  if (!n) return '—';
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(1) + 'k';
  return String(n);
}

function fmtDuration(start, end) {
  const s = Math.round((new Date(end) - new Date(start)) / 1000);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + (s%60) + 's';
  return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
}

function esc(s) { return String(s).replace(/'/g, "\\'"); }
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

// ── Export helpers ──────────────────────────────────────

function toggleExportMenu(e) {
  e.stopPropagation();
  const m = document.getElementById('export-menu');
  if (m) m.classList.toggle('open');
}
function closeExportMenu() {
  const m = document.getElementById('export-menu');
  if (m) m.classList.remove('open');
}
document.addEventListener('click', closeExportMenu);

function exportCSV(type) {
  if (!selected) return;
  const a = document.createElement('a');
  a.href = `/api/agents/${selected}/export/${type}.csv`;
  a.download = `${selected}-${type}.csv`;
  a.click();
}

function exportJSON() {
  if (!selected) return;
  const a = document.createElement('a');
  a.href = `/api/agents/${selected}/export/report.json`;
  a.download = `${selected}-report.json`;
  a.click();
}

// ── Boot ───────────────────────────────────────────────

initTheme();
loadAgents();
setInterval(() => loadAgents(true), 5000);
</script>
</body>
</html>"""

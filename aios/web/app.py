from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from ..runtime.process import ProcessManager as PM


def create_app() -> FastAPI:
    app = FastAPI(title="Ai.os", docs_url=None, redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
                rows = await (await db.execute("SELECT id, status, started_at, ended_at, error FROM agent_runs ORDER BY started_at DESC LIMIT 20")).fetchall()
                return [{"id": r[0][:8], "status": r[1], "started_at": r[2], "ended_at": r[3], "error": r[4]} for r in rows]
            except Exception:
                return []

    @app.get("/api/agents/{name}/logs")
    async def agent_logs(name: str, lines: int = 100) -> dict:
        log_path = PM.log_file(name)
        if not log_path.exists():
            return {"lines": []}
        content = log_path.read_text(errors="replace")
        tail = content.splitlines()[-lines:]
        return {"lines": tail}

    @app.get("/api/agents/{name}/logs/stream")
    async def stream_logs(name: str) -> StreamingResponse:
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
                        yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
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


def _preview(raw: str, length: int = 120) -> str:
    try:
        val = json.loads(raw)
        s = json.dumps(val)
    except Exception:
        s = raw
    return s[:length] + ("…" if len(s) > length else "")


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ai.os</title>
<link rel="icon" href="/favicon.ico" type="image/svg+xml">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#08090F;--s1:#0D0F1A;--s2:#111320;--border:#1C1F33;--border2:#242845;
  --blue:#5B8DF6;--green:#30D483;--red:#E05C5C;--gold:#F0A843;
  --text:#E4EAF8;--sub:#8B91AE;--muted:#484D6A;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;font-size:14px;line-height:1.6}
header{padding:20px 28px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px}
.logo{font-size:18px;font-weight:800;letter-spacing:-.03em;color:#fff}
.logo span{color:var(--blue)}
.header-sub{font-size:12px;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.dot-green{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot-red{background:var(--red)}
.dot-muted{background:var(--muted)}
main{display:grid;grid-template-columns:260px 1fr;height:calc(100vh - 61px)}
.sidebar{border-right:1px solid var(--border);overflow-y:auto;padding:12px 0}
.sidebar-title{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);padding:8px 20px 4px}
.agent-row{display:flex;align-items:center;gap:10px;padding:10px 20px;cursor:pointer;border-left:2px solid transparent;transition:background .15s}
.agent-row:hover{background:var(--s1)}
.agent-row.active{background:var(--s2);border-left-color:var(--blue)}
.agent-name{font-weight:600;font-size:13px}
.agent-meta{font-size:11px;color:var(--muted)}
.panel{padding:24px 28px;overflow-y:auto}
.panel-header{display:flex;align-items:center;gap:12px;margin-bottom:24px}
.panel-title{font-size:17px;font-weight:700;color:#fff}
.badge{font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:2px 8px;border-radius:4px}
.badge-green{background:color-mix(in srgb,var(--green) 14%,transparent);color:var(--green)}
.badge-red{background:color-mix(in srgb,var(--red) 14%,transparent);color:var(--red)}
.badge-muted{background:var(--s2);color:var(--muted)}
.tabs{display:flex;gap:2px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:0}
.tab{padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .15s}
.tab.active{color:var(--blue);border-bottom-color:var(--blue)}
.tab:hover{color:var(--text)}
.tab-content{display:none}.tab-content.active{display:block}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);padding:6px 10px 8px;border-bottom:1px solid var(--border)}
td{padding:10px 10px;border-bottom:1px solid var(--border);color:var(--text);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--s1)}
.mono{font-family:'Courier New',monospace;font-size:12px}
.log-box{background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:16px;height:400px;overflow-y:auto;font-family:'Courier New',monospace;font-size:12px;color:var(--sub);line-height:1.7}
.log-line{padding:1px 0}
.empty{color:var(--muted);font-size:13px;padding:40px 0;text-align:center}
.stat-row{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}
.stat{background:var(--s1);border:1px solid var(--border);border-radius:8px;padding:16px}
.stat-num{font-size:22px;font-weight:800;color:#fff;letter-spacing:-.03em}
.stat-label{font-size:11px;color:var(--muted);margin-top:2px;font-weight:600;letter-spacing:.06em;text-transform:uppercase}
.refresh-btn{margin-left:auto;font-size:12px;color:var(--muted);cursor:pointer;padding:4px 10px;border:1px solid var(--border);border-radius:5px;background:transparent}
.refresh-btn:hover{color:var(--text);border-color:var(--border2)}
#empty-state{padding:60px 28px;color:var(--muted);font-size:14px}
</style>
</head>
<body>
<header>
  <div>
    <div class="logo">Ai<span>.</span>os</div>
    <div class="header-sub">Agent Runtime Dashboard</div>
  </div>
  <button class="refresh-btn" onclick="loadAgents()">↻ Refresh</button>
</header>
<main>
  <div class="sidebar">
    <div class="sidebar-title">Agents</div>
    <div id="agent-list"></div>
  </div>
  <div class="panel" id="main-panel">
    <div id="empty-state">Select an agent to inspect it.</div>
  </div>
</main>

<script>
let agents = [];
let selected = null;
let logEs = null;

async function loadAgents() {
  const res = await fetch('/api/agents');
  agents = await res.json();
  renderSidebar();
  if (selected) showAgent(selected);
}

function renderSidebar() {
  const el = document.getElementById('agent-list');
  if (!agents.length) {
    el.innerHTML = '<div style="padding:16px 20px;color:var(--muted);font-size:12px">No agents found</div>';
    return;
  }
  el.innerHTML = agents.map(a => `
    <div class="agent-row ${selected === a.name ? 'active' : ''}" onclick="showAgent('${a.name}')">
      <span class="dot ${a.running ? 'dot-green' : 'dot-muted'}"></span>
      <div>
        <div class="agent-name">${a.name}</div>
        <div class="agent-meta">${a.running ? 'running' : 'stopped'} · pid ${a.pid || '—'}</div>
      </div>
    </div>
  `).join('');
}

async function showAgent(name) {
  selected = name;
  renderSidebar();

  const agent = agents.find(a => a.name === name) || {name};
  const [memory, runs, logs] = await Promise.all([
    fetch(`/api/agents/${name}/memory`).then(r => r.json()),
    fetch(`/api/agents/${name}/runs`).then(r => r.json()),
    fetch(`/api/agents/${name}/logs?lines=200`).then(r => r.json()),
  ]);

  const running = agent.running;
  document.getElementById('main-panel').innerHTML = `
    <div class="panel-header">
      <div class="panel-title">${name}</div>
      <span class="badge ${running ? 'badge-green' : 'badge-muted'}">${running ? 'running' : 'stopped'}</span>
    </div>
    <div class="stat-row">
      <div class="stat"><div class="stat-num">${runs.length}</div><div class="stat-label">Runs</div></div>
      <div class="stat"><div class="stat-num">${memory.length}</div><div class="stat-label">Memory keys</div></div>
      <div class="stat"><div class="stat-num">${runs.filter(r=>r.status==='completed').length}</div><div class="stat-label">Completed</div></div>
    </div>
    <div class="tabs">
      <div class="tab active" onclick="switchTab('logs')">Logs</div>
      <div class="tab" onclick="switchTab('memory')">Memory</div>
      <div class="tab" onclick="switchTab('runs')">Run History</div>
    </div>
    <div id="tab-logs" class="tab-content active">
      <div class="log-box" id="log-box">${logs.lines.map(l => `<div class="log-line">${escHtml(l)}</div>`).join('') || '<div class="empty">No logs yet</div>'}</div>
    </div>
    <div id="tab-memory" class="tab-content">
      ${memory.length ? `
      <table><thead><tr><th>Key</th><th>Value</th><th>Updated</th></tr></thead><tbody>
      ${memory.map(m => `<tr><td class="mono">${m.key}</td><td class="mono">${escHtml(m.value)}</td><td style="color:var(--muted)">${m.updated_at?.slice(0,19)||''}</td></tr>`).join('')}
      </tbody></table>` : '<div class="empty">Memory is empty</div>'}
    </div>
    <div id="tab-runs" class="tab-content">
      ${runs.length ? `
      <table><thead><tr><th>Run ID</th><th>Status</th><th>Started</th><th>Ended</th></tr></thead><tbody>
      ${runs.map(r => `<tr>
        <td class="mono">${r.id}</td>
        <td><span class="badge ${r.status==='completed'?'badge-green':r.status==='running'?'badge-green':'badge-red'}">${r.status}</span></td>
        <td style="color:var(--muted)">${r.started_at?.slice(0,19)||''}</td>
        <td style="color:var(--muted)">${r.ended_at?.slice(0,19)||'—'}</td>
      </tr>`).join('')}
      </tbody></table>` : '<div class="empty">No runs yet</div>'}
    </div>
  `;

  // Auto-scroll logs
  const lb = document.getElementById('log-box');
  if (lb) lb.scrollTop = lb.scrollHeight;

  // Stream live logs if running
  if (logEs) logEs.close();
  if (running) {
    logEs = new EventSource(`/api/agents/${name}/logs/stream`);
    logEs.onmessage = e => {
      const data = JSON.parse(e.data);
      const lb = document.getElementById('log-box');
      if (!lb) return;
      const line = document.createElement('div');
      line.className = 'log-line';
      line.textContent = data.line;
      lb.appendChild(line);
      lb.scrollTop = lb.scrollHeight;
    };
  }
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((t,i) => t.classList.toggle('active', ['logs','memory','runs'][i] === name));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.getElementById(`tab-${name}`)?.classList.add('active');
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

loadAgents();
setInterval(loadAgents, 5000);
</script>
</body>
</html>"""

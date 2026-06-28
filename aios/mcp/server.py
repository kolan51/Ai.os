"""MCP (Model Context Protocol) server — exposes an Ai.os agent as a tool.

Supports two transports:
  - HTTP/SSE  (default) — compatible with Claude Desktop url-based config
  - stdio     (--stdio) — compatible with Claude Desktop command-based config

Protocol implemented: MCP 2024-11-05 (JSON-RPC 2.0 over SSE or stdio).

No extra dependencies required for stdio mode.
HTTP mode requires `fastapi` and `uvicorn` (already a runtime dependency).
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any


# ── Agent loader ──────────────────────────────────────────────────────────────

def _load_agent_class(agent_file: Path):
    """Import the agent file and return the first Agent subclass found."""
    from ..agent import Agent

    spec = importlib.util.spec_from_file_location("_aios_mcp_agent", agent_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {agent_file}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    for obj in vars(mod).values():
        if (
            inspect.isclass(obj)
            and issubclass(obj, Agent)
            and obj is not Agent
        ):
            return obj
    raise ImportError(f"No Agent subclass found in {agent_file}")


# ── MCP message builders ───────────────────────────────────────────────────────

def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_schema(tool_name: str, tool_description: str, agent_class) -> dict:
    """Build the MCP tools/list entry for this agent."""
    # Inspect the agent's run() signature for parameters, if any
    run_sig = inspect.signature(agent_class.run)
    properties: dict = {}
    required: list = []
    for pname, param in run_sig.parameters.items():
        if pname == "self":
            continue
        ann = param.annotation
        ptype = "string"
        if ann in (int, float):
            ptype = "number"
        elif ann is bool:
            ptype = "boolean"
        properties[pname] = {"type": ptype, "description": pname}
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    # Always include a generic `prompt` param if run() takes no args
    if not properties:
        properties["prompt"] = {
            "type": "string",
            "description": "Task or question to pass to the agent",
        }
        required = ["prompt"]

    desc = tool_description or getattr(agent_class, "description", "") or f"Run the {tool_name} agent"
    return {
        "name": tool_name,
        "description": desc,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# ── Agent invocation ──────────────────────────────────────────────────────────

async def _invoke_agent(agent_class, arguments: dict) -> str:
    """Create a fresh agent instance, bootstrap it, call run(), return result."""
    agent = agent_class.__new__(agent_class)
    agent_class.__init__(agent)
    await agent._bootstrap()

    run_sig = inspect.signature(agent_class.run)
    params = [p for p in run_sig.parameters if p != "self"]

    if params:
        kwargs = {k: arguments.get(k) for k in params if k in arguments}
        result = await agent.run(**kwargs)
    else:
        # Inject prompt into short-term memory if provided
        prompt = arguments.get("prompt", "")
        if prompt:
            agent.memory.set("mcp_prompt", prompt)
        result = await agent.run()

    # Return the result if run() returns a value, else read last memory key
    if result is not None:
        return str(result)
    last = agent.memory.get("result") or agent.memory.get("output") or ""
    return str(last) if last else "(agent completed — no text result returned)"


# ── stdio transport ───────────────────────────────────────────────────────────

async def _stdio_loop(agent_class, tool_name: str, tool_description: str) -> None:
    """Speak MCP over stdin/stdout."""
    tool_entry = _tool_schema(tool_name, tool_description, agent_class)
    reader = asyncio.StreamReader()
    proto = asyncio.StreamReaderProtocol(reader)
    loop = asyncio.get_event_loop()
    await loop.connect_read_pipe(lambda: proto, sys.stdin)

    def _write(obj: dict) -> None:
        line = json.dumps(obj) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    while True:
        try:
            raw = await reader.readline()
        except Exception:
            break
        if not raw:
            break
        try:
            msg = json.loads(raw.decode())
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        req_id = msg.get("id")

        if method == "initialize":
            _write(_ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aios-mcp", "version": "0.1.0"},
            }))

        elif method == "tools/list":
            _write(_ok(req_id, {"tools": [tool_entry]}))

        elif method == "tools/call":
            params = msg.get("params", {})
            if params.get("name") != tool_name:
                _write(_err(req_id, -32602, f"Unknown tool: {params.get('name')}"))
                continue
            try:
                output = await _invoke_agent(agent_class, params.get("arguments", {}))
                _write(_ok(req_id, {
                    "content": [{"type": "text", "text": output}],
                    "isError": False,
                }))
            except Exception as exc:
                _write(_ok(req_id, {
                    "content": [{"type": "text", "text": f"Error: {exc}\n{traceback.format_exc()}"}],
                    "isError": True,
                }))

        elif method == "notifications/initialized":
            pass  # client ack, no response needed

        elif req_id is not None:
            _write(_err(req_id, -32601, f"Method not found: {method}"))


# ── HTTP/SSE transport ────────────────────────────────────────────────────────

async def _sse_server(agent_class, tool_name: str, tool_description: str, port: int) -> None:
    """Serve MCP over HTTP with Server-Sent Events."""
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    mcp_app = FastAPI(title=f"aios-mcp:{tool_name}", docs_url=None)
    tool_entry = _tool_schema(tool_name, tool_description, agent_class)

    # Active SSE sessions: session_id → asyncio.Queue
    _sessions: dict[str, asyncio.Queue] = {}

    @mcp_app.get("/sse")
    async def sse_endpoint(request: Request):
        """SSE channel — client subscribes here, receives JSON-RPC responses."""
        session_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue()
        _sessions[session_id] = q

        # Send the endpoint event so client knows where to POST
        endpoint_url = f"http://localhost:{port}/messages?sessionId={session_id}"

        async def event_stream():
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                _sessions.pop(session_id, None)

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @mcp_app.post("/messages")
    async def messages_endpoint(request: Request, sessionId: str = ""):
        """JSON-RPC POST endpoint — client sends requests here."""
        q = _sessions.get(sessionId)
        if q is None:
            return JSONResponse({"error": "Unknown session"}, status_code=404)

        try:
            msg = await request.json()
        except Exception:
            return JSONResponse({"error": "Bad JSON"}, status_code=400)

        method = msg.get("method", "")
        req_id = msg.get("id")

        async def _respond(obj: dict):
            if q:
                await q.put(obj)

        if method == "initialize":
            await _respond(_ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aios-mcp", "version": "0.1.0"},
            }))
        elif method == "tools/list":
            await _respond(_ok(req_id, {"tools": [tool_entry]}))
        elif method == "tools/call":
            params = msg.get("params", {})
            if params.get("name") != tool_name:
                await _respond(_err(req_id, -32602, f"Unknown tool: {params.get('name')}"))
            else:
                # Run agent in background, stream result back
                async def _run():
                    try:
                        output = await _invoke_agent(agent_class, params.get("arguments", {}))
                        await _respond(_ok(req_id, {
                            "content": [{"type": "text", "text": output}],
                            "isError": False,
                        }))
                    except Exception as exc:
                        await _respond(_ok(req_id, {
                            "content": [{"type": "text", "text": f"Error: {exc}"}],
                            "isError": True,
                        }))
                asyncio.create_task(_run())
        elif method == "notifications/initialized":
            pass
        elif req_id is not None:
            await _respond(_err(req_id, -32601, f"Method not found: {method}"))

        return JSONResponse({"ok": True})

    config = uvicorn.Config(mcp_app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


# ── Public entry point ────────────────────────────────────────────────────────

async def run_mcp_server(
    agent_file: Path,
    tool_name: str,
    tool_description: str,
    port: int = 3000,
    stdio: bool = False,
) -> None:
    agent_class = _load_agent_class(agent_file)

    if stdio:
        await _stdio_loop(agent_class, tool_name, tool_description)
    else:
        await _sse_server(agent_class, tool_name, tool_description, port)

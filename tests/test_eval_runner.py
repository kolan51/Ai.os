"""Tests for the aios eval runner."""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aios.eval.runner import (
    EvalResult,
    _build_mock_response,
    _mock_completion,
    _run_case,
    run_eval_suite,
    _load_agent_class,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _agent_file(tmp_path: Path, src: str) -> Path:
    f = tmp_path / "eval_agent.py"
    f.write_text(textwrap.dedent(src))
    return f


def _make_yaml(tmp_path: Path, content: str, name: str = "eval_agent.eval.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ── mock response builders ────────────────────────────────────────────────────

def test_build_mock_response():
    resp = _build_mock_response("hello world")
    assert resp.choices[0].message.content == "hello world"
    assert resp.choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_mock_completion_single():
    fn = _mock_completion(["answer one"])
    result = await fn(model="x", messages=[])
    assert result.choices[0].message.content == "answer one"


@pytest.mark.asyncio
async def test_mock_completion_cycles_last():
    fn = _mock_completion(["first", "second"])
    r1 = await fn(model="x", messages=[])
    r2 = await fn(model="x", messages=[])
    r3 = await fn(model="x", messages=[])   # stays on last
    assert r1.choices[0].message.content == "first"
    assert r2.choices[0].message.content == "second"
    assert r3.choices[0].message.content == "second"


# ── _run_case ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_case_skip():
    f = _agent_file(Path("."), """
        from aios import Agent
        class A(Agent):
            name = "a"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self): return "hi"
    """)
    cls = _load_agent_class(f)
    r = await _run_case(cls, {"name": "skipped", "skip": True})
    assert r.skipped is True


@pytest.mark.asyncio
async def test_run_case_pass_substring(tmp_path):
    f = _agent_file(tmp_path, """
        from aios import Agent
        class Echo(Agent):
            name = "echo"; model = "claude-haiku-4-5-20251001"; system_prompt = "echo"
            async def run(self):
                return await self.think("say Paris")
    """)
    cls = _load_agent_class(f)
    with patch("litellm.acompletion", _mock_completion(["The capital is Paris"])):
        r = await _run_case(cls, {"name": "capital", "mock_response": "The capital is Paris", "expected": "Paris"})
    assert r.passed is True
    assert "Paris" in r.actual


@pytest.mark.asyncio
async def test_run_case_fail(tmp_path):
    f = _agent_file(tmp_path, """
        from aios import Agent
        class Echo(Agent):
            name = "echo2"; model = "claude-haiku-4-5-20251001"; system_prompt = "echo"
            async def run(self):
                return await self.think("x")
    """)
    cls = _load_agent_class(f)
    with patch("litellm.acompletion", _mock_completion(["wrong answer"])):
        r = await _run_case(cls, {"name": "fails", "mock_response": "wrong answer", "expected": "correct"})
    assert r.passed is False


@pytest.mark.asyncio
async def test_run_case_exact_match(tmp_path):
    f = _agent_file(tmp_path, """
        from aios import Agent
        class E(Agent):
            name = "exact"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self):
                return "hello world"
    """)
    cls = _load_agent_class(f)
    with patch("litellm.acompletion", _mock_completion(["hello world"])):
        r = await _run_case(cls, {"name": "exact", "expected": "hello world", "exact": True})
    assert r.passed is True


@pytest.mark.asyncio
async def test_run_case_memory_seed(tmp_path):
    f = _agent_file(tmp_path, """
        from aios import Agent
        class M(Agent):
            name = "mem"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self):
                city = await self.memory.load("city")
                return city or "unknown"
    """)
    cls = _load_agent_class(f)
    with patch("litellm.acompletion", _mock_completion(["Ljubljana"])):
        r = await _run_case(cls, {
            "name": "mem-seed",
            "memory": {"city": "Ljubljana"},
            "expected": "Ljubljana",
        })
    assert r.passed is True


@pytest.mark.asyncio
async def test_run_case_result_from_memory(tmp_path):
    f = _agent_file(tmp_path, """
        from aios import Agent
        class R(Agent):
            name = "res"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self):
                await self.memory.save("result", "stored answer")
    """)
    cls = _load_agent_class(f)
    with patch("litellm.acompletion", _mock_completion(["x"])):
        r = await _run_case(cls, {"name": "mem-result", "expected": "stored"})
    assert r.passed is True


# ── run_eval_suite ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_suite_passes(tmp_path):
    pytest.importorskip("yaml")
    f = _agent_file(tmp_path, """
        from aios import Agent
        class S(Agent):
            name = "suite"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self): return await self.think("test")
    """)
    suite = _make_yaml(tmp_path, """
cases:
  - name: hello
    mock_response: "hello there"
    expected: "hello"
""")
    with patch("litellm.acompletion", _mock_completion(["hello there"])):
        results = await run_eval_suite(f, suite)
    assert len(results) == 1
    assert results[0].passed is True


@pytest.mark.asyncio
async def test_suite_skip_case(tmp_path):
    pytest.importorskip("yaml")
    f = _agent_file(tmp_path, """
        from aios import Agent
        class S(Agent):
            name = "s2"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self): return "ok"
    """)
    suite = _make_yaml(tmp_path, """
cases:
  - name: skipped case
    skip: true
    expected: anything
""")
    results = await run_eval_suite(f, suite)
    assert results[0].skipped is True


@pytest.mark.asyncio
async def test_suite_update_writes_golden(tmp_path):
    pytest.importorskip("yaml")
    f = _agent_file(tmp_path, """
        from aios import Agent
        class U(Agent):
            name = "u"; model = "claude-haiku-4-5-20251001"; system_prompt = "x"
            async def run(self): return await self.think("x")
    """)
    suite = _make_yaml(tmp_path, """
cases:
  - name: update me
    mock_response: "new output"
    expected: ""
""")
    with patch("litellm.acompletion", _mock_completion(["new output"])):
        results = await run_eval_suite(f, suite, update=True)
    import yaml
    data = yaml.safe_load(suite.read_text())
    assert data["cases"][0]["expected"] == "new output"

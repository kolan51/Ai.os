"""Tests for `aios deploy` CLI command."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aios.cli.main import app

runner = CliRunner()


def test_deploy_docker_creates_files(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    assert (out / "Dockerfile").exists()
    assert (out / "docker-compose.yml").exists()
    assert (out / ".dockerignore").exists()
    assert (out / "deploy.sh").exists()


def test_deploy_dockerfile_content(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out")])
    assert result.exit_code == 0
    df = (tmp_path / "out" / "Dockerfile").read_text()
    assert "FROM python:3.11-slim" in df
    assert "aios-runtime" in df
    assert "myagent.py" in df


def test_deploy_compose_contains_agent_name(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out")])
    assert result.exit_code == 0
    compose = (tmp_path / "out" / "docker-compose.yml").read_text()
    assert "myagent" in compose
    assert "aios-data" in compose


def test_deploy_fly_platform(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out"), "--platform", "fly"])
    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    assert (out / "fly.toml").exists()
    assert (out / "Dockerfile").exists()
    assert not (out / "docker-compose.yml").exists()


def test_deploy_systemd_platform(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out"), "--platform", "systemd"])
    assert result.exit_code == 0, result.output
    out = tmp_path / "out"
    service = list(out.glob("*.service"))
    assert service
    assert "ExecStart" in service[0].read_text()
    assert (out / "install-service.sh").exists()


def test_deploy_invalid_platform(tmp_path):
    result = runner.invoke(app, ["deploy", "--output", str(tmp_path / "out"), "--platform", "k8s"])
    assert result.exit_code != 0
    assert "Unknown platform" in result.output


def test_deploy_output_panel_shown(tmp_path):
    result = runner.invoke(app, ["deploy", "myagent.py", "--output", str(tmp_path / "out")])
    assert "Deploy bundle created" in result.output
    assert "docker compose up" in result.output

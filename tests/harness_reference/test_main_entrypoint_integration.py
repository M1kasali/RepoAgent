"""Main/compat routing and real Node-client to Python-server integration."""

import shutil
import sys
from pathlib import Path

import pytest

from repoagent.entrypoint import main


def test_default_entrypoint_uses_native_runtime_and_restores_argv(monkeypatch):
    from repoagent.harness.cli import commands

    previous = sys.argv
    seen = []
    monkeypatch.setattr(commands, "run", lambda: seen.append(list(sys.argv)))
    assert main(["run", "-m", "hello"]) == 0
    assert seen == [["repoagent", "run", "-m", "hello"]]
    assert sys.argv is previous


def test_compat_entrypoint_is_explicit(monkeypatch):
    from repoagent import cli

    seen = []
    monkeypatch.setattr(cli, "main", lambda args: seen.append(args) or 7)
    assert main(["compat", "--help"]) == 7
    assert seen == [["--help"]]


def test_issue_entrypoint_preserves_case_workflow(monkeypatch):
    from repoagent import cli

    seen = []
    monkeypatch.setattr(cli, "run_product_command", lambda args: seen.append(args) or 0)
    assert main(["issue", "--help"]) == 0
    assert seen == [["issue", "--help"]]


def test_real_typescript_client_calls_python_production_server():
    from repoagent.harness.cli.tui_commands import run_subprocess_with_rpc

    ui = Path(__file__).resolve().parents[2] / "ui-tui"
    node = shutil.which("node")
    if not node or not (ui / "node_modules/tsx").exists():
        pytest.skip("requires Node and npm --prefix ui-tui ci")
    client = (ui / "src/rpc/client.ts").as_uri()
    script = f"""
import {{ RpcClient }} from {client!r};
const timer = setTimeout(() => process.exit(9), 15000);
const client = new RpcClient();
try {{
  await client.ready();
  for (const [method, params] of [
    ['system.hello', {{client_version: '0.1.0'}}],
    ['system.ping', {{}}], ['system.version', {{}}],
    ['setup.status', {{}}], ['terminal.resize', {{cols: 100, rows: 40}}],
  ]) {{
    const result = await client.rpc(method, params);
    if (result === undefined) throw new Error(method + ' missing result');
  }}
  client.close();
  clearTimeout(timer);
}} catch (error) {{ console.error(error); process.exit(1); }}
"""
    assert run_subprocess_with_rpc(
        node, ["--import", "tsx", "--input-type=module", "-e", script],
        cwd=ui, forward_signals=False,
    ) == 0

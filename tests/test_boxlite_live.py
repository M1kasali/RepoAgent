"""Opt-in real microVM smoke; never contacts a model provider."""

import asyncio
import inspect
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import shlex
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic")

from repoagent.boxlite_adapter import BoxliteSandboxAdapter
from repoagent.boxlite_sandbox import SandboxConfig
from repoagent.tool_execution import ToolExecutionControl

pytestmark = pytest.mark.skipif(
    os.environ.get("REPOAGENT_BOXLITE_LIVE") != "1",
    reason="set REPOAGENT_BOXLITE_LIVE=1 for real microVM verification",
)


@pytest.fixture(scope="module")
def boxlite_runtime_home():
    # BoxLite places Unix sockets below this path; pytest's tmp_path is too long.
    # Share the image cache across cases, but never share their working VMs.
    with (
        tempfile.TemporaryDirectory(prefix="ra-bl-", dir="/tmp") as directory,
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        monkeypatch.setenv("REPOAGENT_DATA_DIR", directory)
        try:
            yield directory
        finally:
            from repoagent.boxlite_sandbox._runtime import _runtime_cache

            async def shutdown():
                for key, runtime in list(_runtime_cache.items()):
                    if key[1] == str(Path(directory) / "sandbox/boxlite"):
                        await runtime.shutdown()
                        result = runtime.close()
                        if inspect.isawaitable(result):
                            await result
                        del _runtime_cache[key]

            asyncio.run(shutdown())


def test_real_boxlite_workspace_scratch_timeout_and_cleanup(
    tmp_path, boxlite_runtime_home
):
    print(
        "host:",
        platform.platform(),
        "kvm_exists:",
        os.path.exists("/dev/kvm"),
        "kvm_read_write:",
        os.access("/dev/kvm", os.R_OK | os.W_OK),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = BoxliteSandboxAdapter(
        workspace,
        config=SandboxConfig(
            backend="boxlite",
            image=os.environ.get("REPOAGENT_BOXLITE_IMAGE", "ubuntu:22.04"),
            create_timeout=300,
            verify_timeout=30,
        ),
    )

    def run(command, seconds=5):
        return adapter.execute(
            command,
            cwd=workspace,
            env={},
            control=ToolExecutionControl(
                timeout_seconds=seconds, max_output_chars=4000
            ),
        )

    try:
        adapter.verify_available()
        assert (
            run("echo saved > file.txt; echo scratch > /tmp/repoagent-test").exit_code
            == 0
        )
        assert (workspace / "file.txt").read_text().strip() == "saved"
        assert run("cat /tmp/repoagent-test").stdout.strip() == "scratch"
        assert run("sleep 10", seconds=0.1).status == "timeout"
        assert run("echo alive").stdout.strip() == "alive"
    finally:
        adapter.close_processes()
    assert not adapter.owned_ids
    assert adapter._loop is None
    assert runtime_boxes() == []


def runtime_boxes():
    from repoagent.boxlite_sandbox._runtime import get_boxlite_runtime

    async def snapshot():
        return await get_boxlite_runtime().list_info()

    return asyncio.run(snapshot())


def run(box, command, *, seconds=15, token=None):
    return box.execute(
        command,
        cwd=box.workspace,
        env={},
        control=ToolExecutionControl(
            timeout_seconds=seconds, max_output_chars=16000, cancellation_token=token
        ),
    )


def python_command(source):
    return "exec python3 -u -c " + shlex.quote(source)


def wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    assert predicate(), "condition not reached before deadline"


@pytest.fixture
def boxes(tmp_path, boxlite_runtime_home):
    adapters = []
    pids = set()

    def create(*, policy=True, parent=None, verify_timeout=30):
        workspace = tmp_path / f"workspace-{len(adapters)}"
        workspace.mkdir()
        if parent is not None:
            box = parent.fork(workspace)
        else:
            box = BoxliteSandboxAdapter(
                workspace,
                config=SandboxConfig(
                    backend="boxlite",
                    image=os.environ.get(
                        "REPOAGENT_BOXLITE_TEST_IMAGE", "python:3.12-slim-bookworm"
                    ),
                    allow_net=policy,
                    verify_timeout=verify_timeout,
                ),
            )
        adapters.append(box)
        box.verify_available()
        for info in runtime_boxes():
            assert info.state.running
            assert info.state.pid
            pids.add(info.state.pid)
        return box

    try:
        yield create
    finally:
        for box in reversed(adapters):
            box.close_processes()
        remaining = runtime_boxes()
        assert not remaining, [(b.id, b.state.status) for b in remaining]
        wait_until(lambda: all(not Path(f"/proc/{pid}").exists() for pid in pids))
        assert all(not b.owned_ids and b._thread is None for b in adapters)


MCP_SERVER = """
import json, os, sys, time
from pathlib import Path
Path('/workspace/mcp.pid').write_text(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'real-vm-fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': name, 'description': name,
                  'inputSchema': {'type': 'object', 'properties': {}}}
                  for name in ['exchange', 'block', 'crash']]}
    elif method == 'tools/call':
        name = request['params']['name']
        if name == 'block':
            Path('/workspace/mcp-ready').write_text('ready')
            time.sleep(2)
            Path('/workspace/mcp-late').write_text('leaked')
        if name == 'crash':
            os._exit(7)
        value = Path('/tmp/from-shell').read_text()
        Path('/tmp/from-mcp').write_text('mcp-shared')
        result = {'content': [{'type': 'text', 'text': value}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
"""


def mcp_client(box):
    from repoagent.mcp_transport import StdioMCPClient, StdioServerConfig

    (box.workspace / "mcp_fixture.py").write_text(MCP_SERVER)
    return StdioMCPClient(
        StdioServerConfig("python3", ("-u", "/workspace/mcp_fixture.py")),
        cwd=box.workspace,
        sandbox_adapter=box,
    )


def assert_guest_process_stopped(box, pid):
    result = run(
        box,
        python_command(
            f"from pathlib import Path; p=Path('/proc/{pid}/stat'); "
            "assert not p.exists() or p.read_text().split()[2] == 'Z'"
        ),
    )
    assert result.exit_code == 0, result.stderr


def test_real_mcp_shares_vm_and_closes_only_server(boxes):
    box = boxes()
    vm_id = next(iter(box.owned_ids))
    assert run(box, "echo shell-shared > /tmp/from-shell").exit_code == 0
    client = mcp_client(box)
    try:
        assert {t["name"] for t in client.list_tools()} == {
            "exchange",
            "block",
            "crash",
        }
        result = client.call_tool(
            "exchange",
            {},
            control=ToolExecutionControl(
                timeout_seconds=15,
                max_output_chars=4000,
            ),
        )
        assert result.metadata["exit_code"] == 0, result
        assert result.content.strip() == "shell-shared"
        assert run(box, "cat /tmp/from-mcp").stdout.strip() == "mcp-shared"
        pid = int((box.workspace / "mcp.pid").read_text())
    finally:
        client.close()
    assert_guest_process_stopped(box, pid)
    assert [b.id for b in runtime_boxes()] == [vm_id]
    assert run(box, "echo alive").stdout.strip() == "alive"


@pytest.mark.parametrize("mode", ["cancel", "crash"])
def test_real_mcp_failure_cleans_server_and_preserves_vm(boxes, mode):
    box = boxes()
    assert run(box, "echo shell-shared > /tmp/from-shell").exit_code == 0
    client = mcp_client(box)
    token = SimpleNamespace(cancelled=False)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            client.call_tool(
                "block" if mode == "cancel" else "crash",
                {},
                control=ToolExecutionControl(
                    timeout_seconds=15,
                    max_output_chars=4000,
                    cancellation_token=token,
                ),
            )
        ),
        daemon=True,
    )
    try:
        worker.start()
        if mode == "cancel":
            wait_until(lambda: (box.workspace / "mcp-ready").exists())
            token.cancelled = True
        worker.join(20)
        assert not worker.is_alive()
        assert results, "MCP worker did not return"
        if mode == "cancel":
            assert results[0].metadata["execution_status"] == "cancelled"
        else:
            assert results[0].metadata["exit_code"] == 1
    finally:
        token.cancelled = True
        worker.join(20)
        client.close()
    pid = int((box.workspace / "mcp.pid").read_text())
    assert_guest_process_stopped(box, pid)
    assert run(box, "sleep 2.2; echo alive").stdout.strip() == "alive"
    assert not (box.workspace / "mcp-late").exists()


def test_real_parent_child_vm_isolation(boxes, tmp_path):
    parent = boxes()
    parent_id = next(iter(parent.owned_ids))
    assert (
        run(parent, "echo parent > /tmp/private; echo parent > parent.txt").exit_code
        == 0
    )
    child = boxes(parent=parent)
    child_ids = set(child.owned_ids) - {parent_id}
    assert len(child_ids) == 1
    assert (
        run(
            child, "test ! -e /tmp/private && test ! -e /workspace/parent.txt"
        ).exit_code
        == 0
    )
    assert (
        run(child, "echo child > /tmp/private; echo child > child.txt").exit_code == 0
    )
    assert run(parent, "cat /tmp/private").stdout.strip() == "parent"
    assert not (parent.workspace / "child.txt").exists()
    outside = tmp_path / "host-only"
    outside.write_text("host-only-sentinel")
    assert run(parent, "test ! -e " + shlex.quote(str(outside))).exit_code == 0
    child.close_processes()
    assert [b.id for b in runtime_boxes()] == [parent_id]
    assert run(parent, "cat /tmp/private").stdout.strip() == "parent"


def test_real_timeout_kills_descendants(boxes):
    box = boxes()
    descendant = "import time; from pathlib import Path; time.sleep(2); Path('/workspace/timeout-late').write_text('leaked')"
    command = python_command(
        "import subprocess, time; from pathlib import Path; "
        f"p=subprocess.Popen(['python3','-c',{descendant!r}]); "
        "Path('/workspace/child.pid').write_text(str(p.pid)); time.sleep(30)"
    )
    assert run(box, command, seconds=0.7).status == "timeout"
    assert (box.workspace / "child.pid").exists(), (
        "timeout occurred before process start"
    )
    pid = int((box.workspace / "child.pid").read_text())
    assert run(box, "sleep 2.2; echo alive").stdout.strip() == "alive"
    assert not (box.workspace / "timeout-late").exists(), "descendant survived timeout"
    assert_guest_process_stopped(box, pid)


def test_real_shell_cancellation_removes_vm(boxes):
    box = boxes()
    token = SimpleNamespace(cancelled=False)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            run(
                box,
                python_command(
                    "import time; from pathlib import Path; Path('/workspace/cancel-ready').touch(); "
                    "time.sleep(2); Path('/workspace/cancel-late').write_text('leaked'); time.sleep(30)"
                ),
                seconds=20,
                token=token,
            )
        ),
        daemon=True,
    )
    try:
        worker.start()
        wait_until(lambda: (box.workspace / "cancel-ready").exists())
        token.cancelled = True
        worker.join(15)
        assert not worker.is_alive()
        assert results[0].status == "cancelled"
        assert runtime_boxes() == []
        time.sleep(2.2)
        assert not (box.workspace / "cancel-late").exists()
    finally:
        token.cancelled = True
        worker.join(15)


def test_real_startup_failure_removes_partial_vm(boxes):
    from repoagent.sandbox import SandboxConfigurationError

    # Start an actual VM, then give its verification command no execution time.
    # This exercises the failed-start cleanup without mocking the executor.
    with pytest.raises(SandboxConfigurationError, match="verification timed out"):
        boxes(verify_timeout=0)
    assert runtime_boxes() == []


def network_probe(box, host, *, ip=None):
    # A filtered gateway may accept TCP before inspecting TLS SNI. Require an
    # actual verified HTTPS response, including for the DNS-bypass probe.
    request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
    code = (
        "import json, socket, ssl\n"
        "try:\n"
        f"    raw=socket.create_connection(({ip or host!r},443),timeout=5)\n"
        "    peer=raw.getpeername()[0]\n"
        f"    s=ssl.create_default_context().wrap_socket(raw,server_hostname={host!r})\n"
        f"    s.sendall({request!r})\n"
        "    data=s.recv(1024); s.close()\n"
        "    print(json.dumps({'ok':data.startswith(b'HTTP/'), 'ip':peer}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'ok':False, 'error':str(exc)}))\n"
    )
    result = run(box, python_command(code), seconds=15)
    assert result.exit_code == 0, result
    outcome = json.loads(result.stdout)
    print(
        f"network policy={box.config.allow_net!r} host={host} direct_ip={ip}: {outcome}"
    )
    return outcome


def test_real_network_policy_against_controlled_host(boxes):
    """Test ordinary TCP egress against a controlled host interface, not a website."""
    payload = b"repoagent-boxlite-network-fixture"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    # The SDK treats its special loopback gateway separately. Use the host's
    # routed interface to exercise ordinary egress. UDP connect selects a route
    # without sending traffic to this documentation-only address.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
        route.connect(("192.0.2.1", 9))
        host_ip = route.getsockname()[0]
    server = ThreadingHTTPServer((host_ip, 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.1), daemon=True
    )
    thread.start()
    gateway, port = host_ip, server.server_port

    def probe(box):
        source = (
            "import http.client,json\n"
            "try:\n"
            f"    c=http.client.HTTPConnection({gateway!r},{port},timeout=3)\n"
            "    c.request('GET','/'); r=c.getresponse()\n"
            f"    print(json.dumps({{'ok':r.status==200 and r.read()=={payload!r}}})); c.close()\n"
            "except Exception as e:\n"
            "    print(json.dumps({'ok':False,'error':str(e)}))\n"
        )
        result = run(box, python_command(source))
        assert result.exit_code == 0, result
        return json.loads(result.stdout)

    try:
        for policy, expected in [
            (True, True),
            (False, False),
            ([gateway], True),
            (["192.0.2.1"], False),
        ]:
            box = boxes(policy=policy)
            result = probe(box)
            print(f"controlled network policy={policy!r}: {result}")
            assert result["ok"] is expected, result
            box.close_processes()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.skipif(
    os.environ.get("REPOAGENT_BOXLITE_PUBLIC_NETWORK") != "1",
    reason="set REPOAGENT_BOXLITE_PUBLIC_NETWORK=1 for separate public HTTPS checks",
)
def test_real_public_network_disabled_and_allowlist(boxes):
    allowed, denied = "example.com", "www.python.org"
    baseline = boxes()
    for host in (allowed, denied):
        result = network_probe(baseline, host)
        assert result["ok"], f"unrestricted baseline for {host} failed: {result}"
    ip = result["ip"]
    direct = network_probe(baseline, denied, ip=ip)
    assert direct["ok"], f"direct-IP baseline failed: {direct}"
    baseline.close_processes()
    disabled = boxes(policy=False)
    for host in (allowed, denied):
        assert not network_probe(disabled, host)["ok"], (
            f"disabled network reached {host}"
        )
    assert not network_probe(disabled, denied, ip=ip)["ok"]
    disabled.close_processes()
    restricted = boxes(policy=[allowed])
    result = network_probe(restricted, allowed)
    assert result["ok"], f"allowed host was blocked: {result}"
    assert not network_probe(restricted, denied)["ok"], (
        "non-allowlisted host was reachable"
    )
    assert not network_probe(restricted, denied, ip=ip)["ok"], (
        "direct IP bypassed allowlist"
    )

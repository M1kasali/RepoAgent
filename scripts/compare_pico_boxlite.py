"""Compare unmodified Pico, the port, and its sync adapter on real BoxLite.

Run from the repository with its Python environment. No provider is contacted.
The reference checkout is read-only; each mode uses the same SDK and image cache.
"""

import argparse
import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def close_runtime(module):
    remaining = []
    for runtime in module._runtime_cache.values():
        try:
            remaining.extend(box.id for box in await runtime.list_info())
        finally:
            await runtime.shutdown()
            result = runtime.close()
            if inspect.isawaitable(result):
                await result
    module._runtime_cache.clear()
    assert not remaining, f"VMs remained after executor stop: {remaining}"


async def compare(args, root):
    sys.path.insert(0, str(args.reference))
    from pico.config.loader import set_config_path
    from pico.agent.tools.shell import ExecTool
    from pico.sandbox.boxlite_executor import BoxliteExecutor as Original
    from pico.sandbox import _runtime as original_runtime
    from repoagent.boxlite_sandbox.boxlite_executor import BoxliteExecutor as Port
    from repoagent.boxlite_sandbox import _runtime as port_runtime
    from repoagent.boxlite_adapter import BoxliteSandboxAdapter
    from repoagent.boxlite_sandbox import SandboxConfig
    from repoagent.tool_execution import ToolExecutionControl

    set_config_path(root / "config.json")
    os.environ["REPOAGENT_DATA_DIR"] = str(root)
    manifest = json.loads(Path("LICENSES/pico-boxlite-session-source.json").read_text())
    source = args.reference / "pico/sandbox/boxlite_executor.py"
    expected = next(
        f["upstream_sha256"]
        for f in manifest["files"]
        if f["upstream"] == "pico/sandbox/boxlite_executor.py"
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    report = {
        "sdk": importlib.metadata.version("boxlite"),
        "pico_executor_sha256": expected,
        "image": args.image,
        "modes": {},
    }
    for mode in ("pico", "port", "adapter"):
        workspace = root / mode
        workspace.mkdir()
        entry = {"spawn_errors": [], "successful_commands": 0}
        report["modes"][mode] = entry
        if mode == "adapter":
            instance = BoxliteSandboxAdapter(
                workspace, config=SandboxConfig(backend="boxlite", image=args.image)
            )

            async def start():
                await asyncio.to_thread(instance.verify_available)

            async def execute(command, timeout=15):
                return await asyncio.to_thread(
                    instance.execute,
                    command,
                    cwd=workspace,
                    env={},
                    control=ToolExecutionControl(
                        timeout_seconds=timeout, max_output_chars=4000
                    ),
                )

            async def stop():
                await asyncio.to_thread(instance.close_processes)
        else:
            cls = Original if mode == "pico" else Port
            instance = cls(args.image, workspace)
            start, stop = instance.start, instance.stop

            async def execute(command, timeout=15):
                return await instance.exec(command, cwd=str(workspace), timeout=timeout)

        try:
            await start()

            def vm_id():
                executor = instance._executor if mode == "adapter" else instance
                return executor._box.id if executor and executor._box else None

            initial_vm_id = vm_id()
            for i in range(args.commands):
                try:
                    result = await execute(f"echo round-{i}")
                    assert result.stdout.strip() == f"round-{i}"
                    entry["successful_commands"] += 1
                except Exception as exc:
                    entry["spawn_errors"].append({"command": i, "error": str(exc)})
            entry["same_vm_after_commands"] = vm_id() == initial_vm_id
            descendant = "import time; from pathlib import Path; time.sleep(2); Path('/workspace/late').write_text('leaked')"
            code = (
                "import subprocess,time; from pathlib import Path; "
                f"p=subprocess.Popen(['python3','-c',{descendant!r}]); "
                "Path('/workspace/started').touch(); time.sleep(30)"
            )
            result = await execute("exec python3 -c " + shlex.quote(code), timeout=0.7)
            entry["timeout_exit_code"] = result.exit_code
            entry["process_started"] = (workspace / "started").exists()
            await asyncio.sleep(2.3)
            entry["descendant_wrote_after_timeout"] = (workspace / "late").exists()
            if mode == "pico":
                tool = ExecTool(executor=instance, working_dir=str(workspace))
                tool_code = code.replace(
                    "/workspace/late", "/workspace/tool-late"
                ).replace("/workspace/started", "/workspace/tool-started")
                result = await tool.execute(
                    "exec python3 -c " + shlex.quote(tool_code), timeout=1
                )
                entry["original_shell_tool_result"] = str(result)
                entry["original_shell_tool_failed"] = getattr(result, "failed", None)
                await asyncio.sleep(2.3)
                entry["original_shell_tool_started"] = (
                    workspace / "tool-started"
                ).exists()
                entry["original_shell_tool_descendant_wrote"] = (
                    workspace / "tool-late"
                ).exists()
        except Exception as exc:
            entry["scenario_error"] = str(exc)
        finally:
            await stop()
            await close_runtime(original_runtime if mode == "pico" else port_runtime)
        print(json.dumps({mode: entry}), flush=True)
    # These constructors do not create a VM or mutate reference source.
    import boxlite

    report["reference_network_arguments"] = {}
    for label, kwargs in [
        ("disabled", {"network": "none"}),
        ("allowlist", {"allow_net": ["example.com"]}),
    ]:
        try:
            boxlite.BoxOptions(image=args.image, **kwargs)
            report["reference_network_arguments"][label] = "accepted"
        except Exception as exc:
            report["reference_network_arguments"][label] = str(exc)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--commands", type=int, default=40)
    parser.add_argument("--image", default="python:3.12-slim-bookworm")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pico-ab-", dir="/tmp") as directory:
        report = asyncio.run(compare(args, Path(directory)))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

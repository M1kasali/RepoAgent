import sys
import threading
import time

from repoagent import CancellationToken
from repoagent.tool_execution import (
    ToolExecutionControl,
    limit_output,
    run_bounded_process,
)


def python_command(source):
    return [sys.executable, "-c", source]


def control(*, timeout=2, output=1000, token=None):
    return ToolExecutionControl(
        timeout_seconds=timeout,
        max_output_chars=output,
        cancellation_token=token,
    )


def test_process_runner_completes_and_bounds_retained_output(tmp_path):
    outcome = run_bounded_process(
        python_command("print('x' * 5000)"),
        cwd=tmp_path,
        env={},
        shell=False,
        control=control(output=128),
    )

    assert outcome.status == "completed"
    assert outcome.exit_code == 0
    assert len(outcome.stdout) == 128
    assert outcome.stdout_chars == 5001
    assert outcome.output_truncated is True


def test_timeout_reaps_the_complete_process_group(tmp_path):
    marker = tmp_path / "orphan-timeout.txt"
    child = (
        "import time,pathlib; time.sleep(0.6); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
    )

    outcome = run_bounded_process(
        python_command(parent),
        cwd=tmp_path,
        env={},
        shell=False,
        control=control(timeout=0.1),
    )
    time.sleep(0.7)

    assert outcome.status == "timeout"
    assert outcome.termination_signal in {
        "sigterm",
        "sigkill",
        "taskkill",
        "taskkill_force",
    }
    assert not marker.exists()


def test_cancellation_reaps_the_complete_process_group(tmp_path):
    marker = tmp_path / "orphan-cancel.txt"
    child = (
        "import time,pathlib; time.sleep(0.6); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(5)"
    )
    token = CancellationToken()
    values = []
    worker = threading.Thread(
        target=lambda: values.append(
            run_bounded_process(
                python_command(parent),
                cwd=tmp_path,
                env={},
                shell=False,
                control=control(timeout=2, token=token),
            )
        )
    )
    worker.start()
    time.sleep(0.1)
    token.cancel()
    worker.join(timeout=2)
    time.sleep(0.7)

    assert not worker.is_alive()
    assert values[0].status == "cancelled"
    assert values[0].termination_signal in {
        "sigkill",
        "sigterm",
        "taskkill",
        "taskkill_force",
    }
    assert not marker.exists()


def test_limit_output_preserves_a_bounded_truncation_marker():
    value, truncated, total = limit_output("a" * 500, 80)

    assert truncated is True
    assert total == 500
    assert len(value) <= 80
    assert "truncated" in value
    tiny, tiny_truncated, _ = limit_output("abcdef", 3)
    assert tiny_truncated is True
    assert len(tiny) == 3

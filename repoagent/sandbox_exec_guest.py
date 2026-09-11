"""Standalone Linux guest supervisor, sent through argv without installing files.

The registry coordinates trusted same-user executions, not hostile tenants.
Cancellation tombstones remain until container stop to fence delayed launches.
"""

import fcntl
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


def kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main():
    mode, token, *command = sys.argv[1:]
    if mode not in {"run", "cancel"} or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("invalid execution handle")
    root = Path("/tmp/.repoagent-execs")
    root.mkdir(mode=0o700, exist_ok=True)
    pid_path = root / f"{token}.pid"
    cancelled = root / f"{token}.cancelled"
    with (root / f"{token}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if mode == "cancel":
            cancelled.touch()
            if pid_path.exists():
                kill_group(int(pid_path.read_text()))
            return 0
        if cancelled.exists():
            return 125
        child = subprocess.Popen(command, start_new_session=True)
        try:
            pid_path.write_text(str(child.pid))
        except BaseException:
            kill_group(child.pid)
            child.wait()
            raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            # Keep the group leader unreaped until registry removal. A concurrent
            # cancel cannot hit an unrelated process after PID reuse.
            os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        finally:
            fcntl.flock(lock, fcntl.LOCK_EX)
            pid_path.unlink(missing_ok=True)
            cancelled.touch()
            kill_group(child.pid)
            fcntl.flock(lock, fcntl.LOCK_UN)
        code = child.wait()
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())

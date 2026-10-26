"""Retain a controlling terminal while its foreground Yarn job exits."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import termios
from pathlib import Path
from types import FrameType
from typing import NoReturn


def request_stop(signum: int, frame: FrameType | None) -> NoReturn:
    raise SystemExit(0)


def main() -> None:
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGHUP, request_stop)
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(sys.argv[1:], process_group=0)
        os.tcsetpgrp(0, child.pid)
        state = {
            "supervisor_pid": os.getpid(),
            "supervisor_group": os.getpgrp(),
            "session": os.getsid(0),
            "job_pid": child.pid,
            "job_group": os.getpgid(child.pid),
            "foreground_group": os.tcgetpgrp(0),
        }
        path = Path(os.environ["TASKBOT_TEST_TERMINAL_STATE"])
        path.write_text(json.dumps(state))
        state["yarn_exit"] = child.wait()
        os.tcsetpgrp(0, os.getpgrp())
        path.write_text(json.dumps(state))
        # A real interactive shell stays alive at its prompt. The owner stops us.
        while True:
            signal.pause()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()

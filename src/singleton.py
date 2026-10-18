"""Single-instance process lock via PID file."""
import os
import sys
import psutil
from pathlib import Path
from src.config import DATA_DIR
from src.logger import get_logger

log = get_logger("singleton")


def ensure_single_instance(process_name: str) -> None:
    """
    Ensures only ONE instance of `process_name` runs at any time.
    If an existing instance is alive, terminates it before claiming the lock.
    Writes current PID to `data/{process_name}.pid`.
    """
    pid_file = DATA_DIR / f"{process_name}.pid"
    current_pid = os.getpid()

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid != current_pid and psutil.pid_exists(old_pid):
                try:
                    proc = psutil.Process(old_pid)
                    cmdline = " ".join(proc.cmdline() or [])
                    if process_name in cmdline or "python" in proc.name().lower():
                        log.info(f"Terminating older zombie instance PID {old_pid} for '{process_name}'")
                        proc.kill()
                        proc.wait(timeout=3)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as e:
                    log.warning(f"Could not kill old instance PID {old_pid}: {e}")
        except Exception as e:
            log.debug(f"Error reading old PID file: {e}")

    try:
        pid_file.write_text(str(current_pid))
        log.info(f"Locked single instance for '{process_name}' (PID: {current_pid})")
    except Exception as e:
        log.warning(f"Could not write PID file {pid_file}: {e}")

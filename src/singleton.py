"""Single-instance process lock via PID file.

Contract: the FIRST live claimant wins. A newcomer that finds a live instance
holding the same lock must EXIT, never kill. Killing the holder is what caused
the watchdog↔singleton restart loop: watchdog spawned a replacement while the
old process was still alive, the replacement killed the old one on startup,
the watchdog saw churn and spawned again, every ~20 minutes.
"""
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

    First live claimant wins: if the PID file points at a running process
    whose command line matches this lock, the newcomer logs and EXITS with
    code 0. It never kills the holder — killing caused the restart loop
    where every watchdog spawn murdered its healthy predecessor.

    A stale PID file (PID dead, or PID recycled by an unrelated process)
    is simply overwritten.
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
                    if process_name in cmdline:
                        log.info(
                            f"Another live instance holds '{process_name}' "
                            f"(PID {old_pid}); this process (PID {current_pid}) exits."
                        )
                        sys.exit(0)
                    # PID recycled by an unrelated process — fall through and
                    # overwrite the stale file below.
                    log.info(
                        f"Stale PID file for '{process_name}': PID {old_pid} "
                        f"is now '{proc.name()}'. Overwriting."
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    log.warning(f"Could not inspect old PID {old_pid}: {e}")
        except SystemExit:
            raise
        except Exception as e:
            log.debug(f"Error reading old PID file: {e}")

    try:
        pid_file.write_text(str(current_pid))
        log.info(f"Locked single instance for '{process_name}' (PID: {current_pid})")
    except Exception as e:
        log.warning(f"Could not write PID file {pid_file}: {e}")

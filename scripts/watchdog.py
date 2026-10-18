#!/usr/bin/env python
"""Watchdog: jaminan bot Telegram + organic daemon selalu hidup.

Dijalankan dua lapis:
1. Cronjob Hermes (no_agent) — scheduler cron hidup DI DALAM gateway, jadi
   selama gateway Hermes menyala, watchdog ikut tick tiap beberapa menit.
2. Manual / test: `python watchdog.py --once`.

Idempoten: kalau proses sudah hidup, tidak melakukan apa-apa. stdout HANYA
berisi pesan saat ada aksi (spawn sukses/gagal) — stdout kosong = tidak ada
notifikasi (pola watchdog untuk cron no_agent). Semua kejadian tetap dicatat
ke logs/watchdog.log.

Stdlib-only (tanpa psutil) supaya bisa dijalankan interpreter Python apa pun,
termasuk Python bawaan Hermes.
"""
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR_FALLBACK = Path(r"C:\Users\cubeb\OneDrive\Documents\coding\projects\affiliate-automation")


def _resolve_project_dir() -> Path:
    candidate = Path(__file__).resolve().parents[1]
    if (candidate / "main.py").exists():
        return candidate
    return PROJECT_DIR_FALLBACK


PROJECT_DIR = _resolve_project_dir()
VBS_BOT = PROJECT_DIR / "scripts" / "run_telegram_bot.vbs"
VBS_ORGANIC = PROJECT_DIR / "scripts" / "run_rara_daemon.vbs"
LOG_FILE = PROJECT_DIR / "logs" / "watchdog.log"

ALIVE_WS = 30 * 1024 * 1024   # >30MB working set = proses nyata (bukan trampoline venv)
STALE_AGE = 150               # trampoline kecil & tua = instance mati
SPAWN_GRACE = 20              # detik tunggu setelah spawn

TARGETS = [
    ("bot", "main.py bot", VBS_BOT),
    ("organic", "main.py organic", VBS_ORGANIC),
]

PS_ENUM = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
    "Where-Object { $_.CommandLine -match 'main\\.py' } | "
    "Select-Object ProcessId,CommandLine,WorkingSetSize,"
    "@{n='AgeSec';e={[int]((Get-Date) - $_.CreationDate).TotalSeconds}} | "
    "ConvertTo-Json -Compress"
)


def _log(msg: str, to_stdout: bool = False) -> None:
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [watchdog] {msg}"
    if to_stdout:
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _enum_processes():
    """List python.exe processes whose cmdline mentions main.py (via PowerShell)."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_ENUM],
            capture_output=True, text=True, timeout=60,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception as exc:
        _log(f"enum failed: {exc}")
        return []


def _matches(procs, needle):
    return [p for p in procs if needle in (p.get("CommandLine") or "")]


def _alive(matches):
    return [p for p in matches if (p.get("WorkingSetSize") or 0) > ALIVE_WS]


def _kill(pids):
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        except Exception:
            pass


def _spawn(vbs: Path):
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        ["wscript.exe", str(vbs)],
        cwd=str(PROJECT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def _load_env(path: Path) -> dict:
    env = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip().strip("'\"")
    except OSError:
        pass
    return env


def _notify_telegram(text: str) -> None:
    """Kirim notifikasi langsung via bot Telegram project (thread Threads=9)."""
    env = _load_env(PROJECT_DIR / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID") or env.get("TG_AFFILIATE_CHAT_ID")
    thread_id = env.get("TG_THREADS_THREAD_ID") or env.get("TG_AFFILIATE_THREAD_ID")
    if not token or not chat_id:
        return
    try:
        import urllib.request

        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if thread_id:
            payload["message_thread_id"] = thread_id
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except Exception as exc:
        _log(f"telegram notify failed: {exc}")


def check_once():
    """Heal both targets if needed. Returns list of stdout messages (empty = nothing to report)."""
    messages = []
    procs = _enum_processes()

    for name, needle, vbs in TARGETS:
        matches = _matches(procs, needle)
        alive = _alive(matches)

        if alive:
            _log(f"{name}: alive (pid={alive[0].get('ProcessId')}, "
                 f"ws={(alive[0].get('WorkingSetSize') or 0)//1048576}MB)")
            continue

        if matches:
            oldest = max(int(p.get("AgeSec") or 0) for p in matches)
            if oldest <= STALE_AGE:
                _log(f"{name}: starting (age={oldest}s) — no action")
                continue
            pids = [p.get("ProcessId") for p in matches if p.get("ProcessId")]
            _log(f"{name}: stale trampoline(s) {pids} — killing then respawning")
            _kill(pids)
            time.sleep(2)

        if not vbs.exists():
            msg = f"⚠️ Watchdog: launcher {vbs.name} tidak ditemukan — {name} tidak bisa dijalankan."
            _log(msg, to_stdout=True)
            messages.append(msg)
            continue

        _log(f"{name}: not running — spawning via {vbs.name}")
        _spawn(vbs)
        time.sleep(SPAWN_GRACE)

        procs2 = _enum_processes()
        matches2 = _matches(procs2, needle)
        alive2 = _alive(matches2)
        if alive2:
            msg = f"✅ Watchdog: {name} di-restart (pid={alive2[0].get('ProcessId')})."
            _log(msg, to_stdout=True)
            messages.append(msg)
        elif matches2:
            _log(f"{name}: spawned, masih loading ({len(matches2)} proc)")
        else:
            msg = f"⚠️ Watchdog: gagal menyalakan {name} — tidak ada proses muncul."
            _log(msg, to_stdout=True)
            messages.append(msg)

    return messages


def main():
    # Default: one-shot tick (dipakai cronjob Hermes no_agent / cek manual).
    # stdout hanya berisi pesan aksi (spawn/gagal) — stdout kosong = tidak ada
    # notifikasi, cocok untuk pola watchdog cron. --verbose menampilkan "OK" juga.
    if "--loop" in sys.argv:
        _log("watchdog started (loop mode)")
        while True:
            try:
                check_once()
            except Exception as exc:
                _log(f"cycle error: {exc}")
            time.sleep(60)
        return

    msgs = check_once()
    for msg in msgs:
        _notify_telegram(msg)
    if "--verbose" in sys.argv:
        print("OK — semua target hidup")

if __name__ == "__main__":
    main()

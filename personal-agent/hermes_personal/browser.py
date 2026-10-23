"""Own one dedicated local Chromium; never attach to or close a personal browser."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

PORT = 9223


def browser_version():
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}/json/version")
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


def ensure_browser(home, *, visible=False):
    home = Path(home).resolve()
    record_path = home / "personal-browser-process.json"
    profile = home / "personal-browser"
    profile.mkdir(exist_ok=True)
    executables = sorted((home / "tools").glob("chromium-*/chrome-win64/chrome.exe"))
    if not executables:
        raise RuntimeError("Managed Chromium is missing; run the setup script")
    executable = executables[-1].resolve()
    running = None
    try:
        running = browser_version()
    except (OSError, ValueError):
        pass
    if running:
        import psutil
        if not record_path.exists():
            raise RuntimeError("Port 9223 is occupied by an unowned browser; choose a free port locally")
        record = json.loads(record_path.read_text())
        process = psutil.Process(record["pid"])
        command = process.cmdline()
        if Path(process.exe()).resolve() != executable or f"--user-data-dir={profile}" not in command or f"--remote-debugging-port={PORT}" not in command:
            raise RuntimeError("Local browser listener identity does not match the dedicated Hermes profile")
        is_headless = any(arg.startswith("--headless") for arg in command)
        if visible and is_headless:
            # The user explicitly requested the dedicated browser window. Close
            # only the owned CDP browser, preserving its profile and other apps.
            from websockets.sync.client import connect
            with connect(running["webSocketDebuggerUrl"], open_timeout=3) as connection:
                connection.send(json.dumps({"id": 1, "method": "Browser.close"}))
            for _ in range(30):
                if not process.is_running():
                    break
                time.sleep(.1)
            if process.is_running():
                raise RuntimeError("Dedicated headless browser did not close; no process was killed")
        else:
            return {"ok": True, "reused": True, "port": PORT, "pid": process.pid, "visible": not is_headless}
    flags = [str(executable), f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}", "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check", "--disable-default-apps", "--disable-component-update", "about:blank"]
    if not visible:
        flags.insert(1, "--headless=new")
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup is not None and not visible:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    process = subprocess.Popen(flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == "nt" and not visible else 0, startupinfo=startup)
    record_path.write_text(json.dumps({"pid": process.pid, "executable": str(executable), "profile": str(profile)}), encoding="utf-8")
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("Dedicated Chromium exited during startup")
        try:
            browser_version()
            return {"ok": True, "reused": False, "port": PORT, "pid": process.pid, "visible": visible}
        except (OSError, ValueError):
            time.sleep(.1)
    raise RuntimeError("Dedicated browser did not expose its local CDP endpoint")

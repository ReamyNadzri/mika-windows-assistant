"""Non-blocking local speech mailbox; no speech dependencies in the gateway."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else (default or {})
    except (OSError, ValueError):
        return default or {}


def short_spoken_reply(text):
    """Speak the complete opening summary and any closing questions."""
    if not isinstance(text, str) or text.lstrip().startswith(("<tool_call", "<｜｜DSML", "<|DSML")):
        return ""
    text = re.sub(r"```[\s\S]*?(?:```|$)", "\n\n", text)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+|[A-Za-z]:[\\/]\S+|MEDIA:\S+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]+.*$", "", text)
    text = re.sub(r"(?m)^[ \t]*(?:[-*•][ \t]+|\d+[.)][ \t]+)", "", text)
    text = re.sub(r"[*_~|]", "", text)
    paragraphs = [" ".join(part.split()).strip(" :;-\n")
                  for part in re.split(r"\n[ \t]*\n", text)]
    paragraphs = [part for part in paragraphs if any(character.isalnum() for character in part)]
    if not paragraphs:
        return ""
    summary = paragraphs[0]
    if len(paragraphs) > 1:
        closing = paragraphs[-1]
        # Periods in names, titles and initials are not sentence endings.
        protected = re.sub(r"\b(?:Dr|Mr|Mrs|Ms|Prof|Sr|Jr|St|vs|etc)\.|\b[A-Z]\.",
                           lambda match: match.group().replace(".", "\ue000"), closing, flags=re.IGNORECASE)
        for part in re.split(r"(?<=[.!?])\s+", protected):
            sentence = part.replace("\ue000", ".")
            if sentence.endswith("?") and sentence.casefold() not in summary.casefold():
                summary += " " + sentence
    # No word count or duration cutoff: finish every selected sentence.
    return summary


def worker_environment():
    # Gateway credentials must not be inherited by the desktop/audio process.
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
               "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PATH"}
    result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    result.update(PYTHONUTF8="1", HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                  TOKENIZERS_PARALLELISM="false", GRADIO_ANALYTICS_ENABLED="False")
    return result


def voice_assets(settings):
    """Resolve portable assets without importing any speech dependencies."""
    if not settings.get("assets_dir"):
        raise ValueError("Voice needs assets_dir; run scripts/setup_voice.py first")
    assets = Path(settings["assets_dir"]).expanduser().resolve()
    interpreter = Path(settings["python"]).expanduser().resolve() if settings.get("python") else assets / "env/Scripts/pythonw.exe"
    if not settings.get("python") and not interpreter.is_file():
        interpreter = assets / "env/Scripts/python.exe"
    voice_id = settings.get("voice_id", "bm_george")
    if not isinstance(voice_id, str) or not re.fullmatch(r"[a-z]{2}_[a-z0-9_]+", voice_id):
        raise ValueError("Use a built-in Kokoro voice ID, such as bm_george")
    voice_file = Path(settings["voice_file"]).expanduser().resolve() if settings.get("voice_file") else None
    for asset in (interpreter, assets / "models/kokoro-v1.0.onnx", assets / "models/voices-v1.0.bin", voice_file):
        if asset is not None and (not asset.is_file() or not asset.stat().st_size):
            raise FileNotFoundError("Mika voice asset missing or empty: " + str(asset))
    return {"assets_dir": assets, "python": interpreter, "voice_id": voice_id, "voice_file": voice_file}


class VoiceService:
    def __init__(self, home, settings):
        self.home = Path(home).resolve()
        self.folder = self.home / "voice"
        self.settings = dict(settings or {})
        self._mutex = threading.RLock()
        self._worker = None
        self._last_key = None

    @property
    def enabled(self):
        return bool(read_json(self.folder / "preferences.json").get("enabled", self.settings.get("enabled", False)))

    def _request(self, action, **data):
        record = {"id": uuid.uuid4().hex, "action": action, "created_at": time.time(), **data}
        atomic_json(self.folder / "request.json", record)
        return record

    def cancel(self):
        with self._mutex:
            self._request("cancel")

    def set_enabled(self, enabled):
        with self._mutex:
            atomic_json(self.folder / "preferences.json", {"enabled": bool(enabled)})
            if not enabled:
                self.cancel()

    def publish(self, text, *, key):
        spoken = short_spoken_reply(text)
        with self._mutex:
            if not self.enabled or not spoken or key == self._last_key:
                return False
            self.ensure_worker()
            self._request("speak", text=spoken, key=str(key))
            self._last_key = key
            return True

    def ensure_worker(self):
        if self._worker is not None and self._worker.poll() is None:
            return
        import psutil
        info = read_json(self.folder / "worker.json")
        script = Path(__file__).with_name("voice_worker.py").resolve()
        try:
            process = psutil.Process(int(info["pid"]))
            command = process.cmdline()
            if str(script) in command and str(self.home) in command and int(info.get("parent_pid", 0)) == os.getpid():
                return
        except (KeyError, ValueError, psutil.Error):
            pass
        assets = voice_assets(self.settings)
        command = [str(assets["python"]), str(script), "--home", str(self.home),
                   "--assets-dir", str(assets["assets_dir"]), "--voice-id", assets["voice_id"],
                   "--parent-pid", str(os.getpid())]
        if assets["voice_file"]:
            command.extend(["--voice-file", str(assets["voice_file"])])
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / "worker.log").open("a", encoding="utf-8") as log:
            self._worker = subprocess.Popen(
                command, cwd=str(self.folder), env=worker_environment(),
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

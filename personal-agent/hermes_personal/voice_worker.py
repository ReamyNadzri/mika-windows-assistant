"""Persistent Kokoro voice, bounded local playback and desktop orb. No network."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time

try:
    from .voice import atomic_json, read_json, short_spoken_reply
except ImportError:
    from voice import atomic_json, read_json, short_spoken_reply


def take_request(folder, *, now=None):
    folder = Path(folder)
    record = read_json(folder / "request.json")
    token = record.get("id")
    if not isinstance(token, str) or not re.fullmatch(r"[\w-]{1,64}", token):
        return None
    if read_json(folder / "claimed.json").get("id") == token:
        return None
    atomic_json(folder / "claimed.json", {"id": token})
    try:
        age = (time.time() if now is None else now) - float(record["created_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(age) or not -5 <= age <= 45 or record.get("action") not in {"speak", "cancel", "shutdown"}:
        return None
    if record["action"] == "speak":
        record["text"] = short_spoken_reply(record.get("text"))
        if not record["text"]:
            return None
    return record


def is_current_request(folder, token, *, now=None):
    folder = Path(folder)
    latest = read_json(folder / "request.json")
    try:
        age = (time.time() if now is None else now) - float(latest["created_at"])
    except (KeyError, TypeError, ValueError):
        return False
    return (latest.get("id") == token and latest.get("action") == "speak"
            and math.isfinite(age) and -5 <= age <= 45
            and read_json(folder / "preferences.json").get("enabled", True))


def bounded_audio(audio, rate):
    """Validate and normalize the complete waveform, preserving its ending."""
    import numpy as np

    audio = np.asarray(audio, dtype="float32").reshape(-1).copy()
    if rate <= 0 or not audio.size or not np.isfinite(audio).all():
        raise ValueError("Voice returned invalid audio")
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio *= 0.85/peak
    return audio


def load_voice_style(path):
    """Read only an explicitly supplied compatible numeric Kokoro style."""
    if path is None:
        return None
    import numpy as np
    style = np.load(path, allow_pickle=False)
    if not isinstance(style, np.ndarray) or style.shape != (510, 1, 256) or style.dtype.kind not in "fi" or not np.isfinite(style).all():
        raise ValueError("Voice style must be a finite numeric .npy array of shape (510, 1, 256)")
    return style.astype("float32", copy=False)


class KokoroRenderer:
    def __init__(self, assets_dir, *, voice_id="bm_george", voice_file=None):
        self.assets_dir = Path(assets_dir)
        self.voice_id, self.voice_file = voice_id, voice_file
        self.style = None
        self.engine = None

    def load(self):
        if self.engine is not None:
            return
        import onnxruntime as rt
        from kokoro_onnx import Kokoro

        models = self.assets_dir / "models"
        options = rt.SessionOptions()
        options.intra_op_num_threads = max(2, min(6, int((os.cpu_count() or 4)*0.75)))
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        session = rt.InferenceSession(str(models / "kokoro-v1.0.onnx"), options,
                                      providers=["CPUExecutionProvider"])
        self.style = load_voice_style(self.voice_file)
        self.engine = Kokoro.from_session(session, str(models / "voices-v1.0.bin"))
        if self.style is None and self.voice_id not in self.engine.get_voices():
            raise ValueError("Built-in voice not found: " + self.voice_id)

    def render(self, text):
        self.load()
        voice = self.style if self.style is not None else self.voice_id
        language = "en-us" if self.voice_id.startswith("a") else "en-gb"
        audio, rate = self.engine.create(text, voice=voice, speed=1.0, lang=language)
        return bounded_audio(audio, rate), rate


class VoiceWorker:
    def __init__(self, root, home, assets_dir, parent_alive, *, voice_id="bm_george", voice_file=None):
        try:
            from .voice_orb import SpeakingOrb
        except ImportError:
            from voice_orb import SpeakingOrb
        self.root, self.folder = root, Path(home) / "voice"
        self.parent_alive = parent_alive
        self.folder.mkdir(parents=True, exist_ok=True)
        self.renderer = KokoroRenderer(assets_dir, voice_id=voice_id, voice_file=voice_file)
        self.orb = SpeakingOrb(root)
        self.events = queue.Queue()
        self.current, self.pending = None, None
        self.rendering, self.playing = True, False
        self.finished = False
        self.status("loading")
        threading.Thread(target=self._warm, daemon=True).start()
        root.after(50, self.tick)

    def status(self, state, **data):
        atomic_json(self.folder / "status.json", {"state": state, "at": time.time(),
                    "request_id": self.current, "orb_hwnd": int(self.orb.hwnd), **data})

    def _warm(self):
        try:
            self.renderer.load()
            self.renderer.render("Ready.")  # Prime phonemization/inference without playing audio.
            self.events.put(("warm", None, None))
        except Exception as exc:
            self.events.put(("error", None, str(exc)))

    def _render(self, request):
        try:
            audio, rate = self.renderer.render(request["text"])
            self.events.put(("rendered", request["id"], (audio, rate)))
        except Exception as exc:
            self.events.put(("error", request["id"], str(exc)))

    def stop_audio(self):
        import winsound
        winsound.PlaySound(None, 0)
        self.playing = False
        self.orb.hide()

    def _start_audio(self, token, audio, rate):
        if not is_current_request(self.folder, token) or not self.parent_alive():
            return
        import soundfile as sf
        target = self.folder / ("audio-" + token + ".wav")
        sf.write(target, audio, rate, subtype="PCM_16")
        self.playing = True
        self.orb.show(audio, rate)
        self.status("speaking", duration=len(audio)/rate, audio_file=str(target),
                    started_at=time.time())

        def play():
            # Start and stop both run on Tk's thread. A cancellation cannot be
            # consumed between validation and starting an asynchronous sound.
            import winsound
            try:
                if token != self.current or not is_current_request(self.folder, token):
                    self.events.put(("ended", token, None))
                    return
                winsound.PlaySound(str(target), winsound.SND_FILENAME | winsound.SND_NODEFAULT | winsound.SND_ASYNC)
                self.root.after(math.ceil(len(audio)/rate*1000),
                                lambda: self.events.put(("ended", token, None)))
            except Exception as exc:
                self.events.put(("ended", token, str(exc)))
        self.root.after(25, play)

    def tick(self):
        if not self.parent_alive():
            self.shutdown()
            return
        request = take_request(self.folder)
        if request:
            self.current = request["id"]
            self.stop_audio()
            self.pending = request if request["action"] == "speak" else None
            if request["action"] == "shutdown":
                self.shutdown()
                return
            self.status("rendering" if self.pending else "idle")
        while True:
            try:
                kind, token, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind in {"rendered", "warm", "error"}:
                self.rendering = False
            if kind == "rendered" and token == self.current and is_current_request(self.folder, token):
                self._start_audio(token, *value)
            elif kind == "warm" and not self.pending:
                self.status("idle")
            elif kind == "error":
                self.orb.hide()
                self.status("error", error=str(value)[:500])
            elif kind == "ended":
                if token == self.current:
                    self.playing = False
                    self.orb.hide()
                    self.status("error" if value else "idle", **({"error": value} if value else {}))
                try:
                    (self.folder / ("audio-" + token + ".wav")).unlink(missing_ok=True)
                except OSError:
                    pass
        if self.pending and not self.rendering:
            request, self.pending = self.pending, None
            if is_current_request(self.folder, request["id"]):
                self.rendering = True
                self.status("rendering")
                threading.Thread(target=self._render, args=(request,), daemon=True).start()
        if not self.finished:
            self.root.after(70, self.tick)

    def shutdown(self):
        if self.finished:
            return
        self.finished = True
        self.stop_audio()
        self.status("stopped")
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--voice-id", default="bm_george")
    parser.add_argument("--voice-file", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Mika's desktop voice companion requires Windows")
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      TOKENIZERS_PARALLELISM="false", GRADIO_ANALYTICS_ENABLED="False")
    (args.home / "voice").mkdir(parents=True, exist_ok=True)
    for stream in ("stdout", "stderr"):
        if getattr(sys, stream) is None:
            setattr(sys, stream, (args.home / "voice/worker.log").open("a", encoding="utf-8"))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    name = "Local\\MikaVoice-" + hashlib.sha256(str(args.home.resolve()).encode()).hexdigest()[:20]
    mutex = kernel.CreateMutexW(None, 1, name)
    if not mutex or ctypes.get_last_error() == 183:
        if mutex:
            kernel.CloseHandle(mutex)
        return
    parent = kernel.OpenProcess(0x1000, 0, args.parent_pid)
    def alive():
        code = ctypes.c_uint32()
        return bool(parent and kernel.GetExitCodeProcess(parent, ctypes.byref(code)) and code.value == 259)
    try:
        if not alive():
            return
        import tkinter as tk
        atomic_json(args.home / "voice/worker.json", {"pid": os.getpid(), "parent_pid": args.parent_pid})
        root = tk.Tk()
        worker = VoiceWorker(root, args.home, args.assets_dir, alive,
                             voice_id=args.voice_id, voice_file=args.voice_file)
        root.protocol("WM_DELETE_WINDOW", worker.shutdown)
        root.mainloop()
    finally:
        if parent:
            kernel.CloseHandle(parent)
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()

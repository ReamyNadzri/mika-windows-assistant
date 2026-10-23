"""Local-only speaking/orb acceptance probe. Never contacts Telegram or the model API."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_personal.plugin import PersonalAssistant
from hermes_personal.voice import VoiceService, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=Path(__file__).resolve().parents[2] / ".local/voice")
    parser.add_argument("--voice-id", default="bm_george")
    parser.add_argument("--voice-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "workspace/voice-validation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    user = ctypes.windll.user32
    user.GetForegroundWindow.restype = ctypes.c_void_p
    user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def wait_for(home, state, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = read_json(home / "voice/status.json")
            if status.get("state") == "error":
                raise RuntimeError(status.get("error", "Voice worker failed"))
            if status.get("state") == state:
                return status
            time.sleep(0.08)
        log = home / "voice/worker.log"
        raise TimeoutError("Voice did not reach " + state + ": " + (log.read_text(encoding="utf-8", errors="replace")[-1500:] if log.exists() else "No worker log"))

    with tempfile.TemporaryDirectory(prefix="mika-voice-check-") as work:
        home = Path(work)
        settings = {"enabled": True, "assets_dir": str(args.assets_dir), "voice_id": args.voice_id}
        if args.voice_file:
            settings["voice_file"] = str(args.voice_file)
        service = VoiceService(home, settings)
        try:
            service.cancel()
            service.ensure_worker()
            wait_for(home, "idle")
            foreground = user.GetForegroundWindow()
            assistant = PersonalAssistant(home, "42", [home], voice=service)
            assistant.before_turn(session_id="local-acceptance", turn_id="voice-demo", platform="telegram",
                                  sender_id="42", user_message="Try the new voice")
            assistant.on_interim_message(session_id="local-acceptance", turn_id="voice-demo", surface="telegram",
                                         text="I'll check your inbox now, sir.")
            acknowledgement = wait_for(home, "speaking")
            assert read_json(home / "voice/request.json")["text"] == "I'll check your inbox now, sir."
            wait_for(home, "idle", timeout=acknowledgement["duration"]+5)
            assert assistant.store.get(assistant.current_task("local-acceptance"))["status"] == "running"
            started = time.monotonic()
            summary = ("Of course, sir. I have removed the old speech limits so that an important answer can reach its natural ending. "
                       "I will keep everyday replies brief, while allowing enough time to say everything you need to hear.")
            question = "Would you like me to explain the next step?"
            complete_reply = summary + " " + question
            written_reply = summary + "\n\nDetailed report: this paragraph stays in the written chat and should not be spoken.\n\n" + question
            assistant.after_turn(session_id="local-acceptance", turn_id="voice-demo",
                                 assistant_response=written_reply)
            speaking = wait_for(home, "speaking")
            assert read_json(home / "voice/request.json")["text"] == complete_reply
            latency = time.monotonic()-started
            hwnd = speaking["orb_hwnd"]
            styles = user.GetWindowLongW(hwnd, -20)
            assert styles & 0x20 and styles & 0x08000000
            assert user.GetForegroundWindow() == foreground, "Speaking orb stole focus"
            assert speaking["duration"] > 10, "Probe must exercise the old ten-second cutoff"
            assert user.IsWindowVisible(ctypes.c_void_p(hwnd))
            from ctypes import wintypes
            from PIL import ImageGrab
            rect = wintypes.RECT()
            user.GetWindowRect(hwnd, ctypes.byref(rect))
            screenshot = args.output / "mika-speaking-orb.png"
            ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(screenshot)
            wait_for(home, "idle", timeout=speaking["duration"]+5)
            playback_elapsed = time.time()-speaking["started_at"]
            assert playback_elapsed >= speaking["duration"], "Playback ended before the waveform finished"
            assert not user.IsWindowVisible(ctypes.c_void_p(hwnd)), "Orb stayed visible after playback"
            service.publish("I can keep this brief. You can interrupt my voice at any time by sending another message.", key="cancel-demo")
            wait_for(home, "speaking")
            time.sleep(0.3)
            cancelled_at = time.monotonic()
            service.cancel()
            wait_for(home, "idle", timeout=2)
            cancellation_seconds = time.monotonic() - cancelled_at
            assert cancellation_seconds < 1 and not user.IsWindowVisible(ctypes.c_void_p(hwnd))
            report = {"final_hook": True, "local_playback_completed": True,
                      "initial_acknowledgement_spoken": True,
                      "acknowledgement_duration": acknowledgement["duration"],
                      "complete_reply_preserved": True, "played_past_ten_seconds": True,
                      "summary_and_closing_question_only": True,
                      "playback_elapsed_seconds": round(playback_elapsed, 2),
                      "duration": speaking["duration"], "speech_ready_seconds": round(latency, 2),
                      "no_focus_steal": True, "click_through": True, "orb_hidden_after_playback": True,
                      "playback_cancelled": True, "cancellation_seconds": round(cancellation_seconds, 2),
                      "telegram_messages_sent": 0, "screenshot": str(screenshot)}
            (args.output / "voice-acceptance.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
        finally:
            service._request("shutdown")
            if service._worker:
                try:
                    service._worker.wait(timeout=8)
                except Exception:
                    service._worker.terminate()
                    service._worker.wait(timeout=5)


if __name__ == "__main__":
    main()

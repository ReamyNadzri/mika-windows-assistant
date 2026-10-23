import json

import numpy as np
import pytest

from hermes_personal.voice import atomic_json
from hermes_personal.voice_worker import bounded_audio, take_request, is_current_request


def test_expired_claimed_and_future_requests_do_not_replay(tmp_path):
    request = {"id": "first", "action": "speak", "created_at": 50, "text": "Hello."}
    atomic_json(tmp_path / "request.json", request)
    assert take_request(tmp_path, now=100) is None
    request["created_at"] = 99
    atomic_json(tmp_path / "request.json", request)
    assert take_request(tmp_path, now=100) is None  # Claimed stale IDs cannot be revived.
    request["id"] = "fresh"
    atomic_json(tmp_path / "request.json", request)
    assert take_request(tmp_path, now=100)["text"] == "Hello."
    assert take_request(tmp_path, now=100) is None
    request.update(id="future", created_at=110)
    atomic_json(tmp_path / "request.json", request)
    assert take_request(tmp_path, now=100) is None


def test_cancellation_during_synthesis_invalidates_old_audio(tmp_path):
    atomic_json(tmp_path / "request.json", {"id": "old", "action": "speak", "created_at": 99, "text": "Old."})
    assert is_current_request(tmp_path, "old", now=100)
    atomic_json(tmp_path / "request.json", {"id": "cancel", "action": "cancel", "created_at": 100})
    assert not is_current_request(tmp_path, "old", now=100)
    atomic_json(tmp_path / "request.json", {"id": "new", "action": "speak", "created_at": 100, "text": "New."})
    assert not is_current_request(tmp_path, "old", now=100) and is_current_request(tmp_path, "new", now=100)


def test_audio_that_expires_during_render_cannot_start(tmp_path):
    atomic_json(tmp_path / "request.json", {"id": "slow", "action": "speak", "created_at": 100})
    assert is_current_request(tmp_path, "slow", now=101)
    assert not is_current_request(tmp_path, "slow", now=146)
    assert not is_current_request(tmp_path, "slow", now=90)


def test_cancel_before_delayed_playback_never_starts_old_sound(tmp_path, monkeypatch):
    import queue
    import sys
    import time
    from types import SimpleNamespace
    from hermes_personal.voice_worker import VoiceWorker
    callbacks, sounds = [], []
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "winsound", SimpleNamespace(
        PlaySound=lambda *args: sounds.append(args), SND_FILENAME=1, SND_NODEFAULT=2, SND_ASYNC=4))
    worker = VoiceWorker.__new__(VoiceWorker)
    worker.folder, worker.current = tmp_path, "old"
    worker.parent_alive = lambda: True
    worker.root = SimpleNamespace(after=lambda delay, callback: callbacks.append(callback))
    worker.orb = SimpleNamespace(show=lambda *a: None, hide=lambda: None)
    worker.status = lambda *a, **k: None
    worker.events = queue.Queue()
    atomic_json(tmp_path / "request.json", {"id": "old", "action": "speak", "created_at": time.time()})
    worker._start_audio("old", np.ones(1000, dtype="float32"), 1000)
    atomic_json(tmp_path / "request.json", {"id": "cancel", "action": "cancel", "created_at": time.time()})
    worker.current = "cancel"
    worker.stop_audio()
    callbacks.pop(0)()
    assert sounds == [(None, 0)]


def test_playback_is_async_on_the_same_controller_thread(tmp_path, monkeypatch):
    import queue
    import sys
    import time
    from types import SimpleNamespace
    from hermes_personal.voice_worker import VoiceWorker
    callbacks, sounds = [], []
    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "winsound", SimpleNamespace(
        PlaySound=lambda *args: sounds.append(args), SND_FILENAME=1, SND_NODEFAULT=2, SND_ASYNC=4))
    worker = VoiceWorker.__new__(VoiceWorker)
    worker.folder, worker.current = tmp_path, "new"
    worker.parent_alive = lambda: True
    worker.root = SimpleNamespace(after=lambda delay, callback: callbacks.append(callback))
    worker.orb = SimpleNamespace(show=lambda *a: None, hide=lambda: None)
    worker.status = lambda *a, **k: None
    worker.events = queue.Queue()
    atomic_json(tmp_path / "request.json", {"id": "new", "action": "speak", "created_at": time.time()})
    worker._start_audio("new", np.ones(1000, dtype="float32"), 1000)
    callbacks.pop(0)()
    assert sounds[0][1] & 4
    worker.stop_audio()
    assert sounds[-1] == (None, 0)


def test_complete_audio_is_preserved_past_ten_seconds_and_pauses():
    sr = 1000
    audio = np.full(12000, 0.1, dtype="float32")
    audio[9400:9500] = 0
    bounded = bounded_audio(audio, sr)
    assert len(bounded) == len(audio)
    assert np.isfinite(bounded).all() and bounded[-1] > 0
    assert np.count_nonzero(bounded[9400:9500]) == 0
    no_pause = bounded_audio(np.ones(12000, dtype="float32"), sr)
    assert len(no_pause) == 12000 and no_pause[-1] > 0


def test_short_audio_preserves_pace_and_has_safe_levels():
    raw = np.sin(np.arange(1000) * 0.1).astype("float32")
    bounded = bounded_audio(raw, 1000)
    assert len(bounded) == len(raw) and np.max(np.abs(bounded)) <= 0.9


def test_renderer_uses_public_builtin_voice_without_style_file(tmp_path):
    from hermes_personal.voice_worker import KokoroRenderer
    from types import SimpleNamespace
    calls = []
    renderer = KokoroRenderer(tmp_path, voice_id="bm_george")
    renderer.engine = SimpleNamespace(create=lambda text, **kwargs: (
        calls.append((text, kwargs)) or np.ones(10, dtype="float32"), 24000))
    renderer.render("Ready.")
    assert calls == [("Ready.", {"voice": "bm_george", "speed": 1.0, "lang": "en-gb"})]


def test_custom_style_is_optional_and_never_loads_pickle(tmp_path):
    from hermes_personal.voice_worker import load_voice_style
    assert load_voice_style(None) is None
    valid = tmp_path / "own.npy"
    np.save(valid, np.ones((510, 1, 256), dtype="float32"))
    assert load_voice_style(valid).shape == (510, 1, 256)
    for value in (np.ones((1, 256)), np.full((510, 1, 256), np.nan), np.array([{"code": "unsafe"}], dtype=object)):
        np.save(valid, value)
        with pytest.raises(ValueError):
            load_voice_style(valid)

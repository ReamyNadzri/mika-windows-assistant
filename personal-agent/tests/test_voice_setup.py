"""Setup checks never download a model, install a package or play audio."""
import hashlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def setup_module():
    path = Path(__file__).parents[1] / "scripts/setup_voice.py"
    spec = importlib.util.spec_from_file_location("mika_setup_voice", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_setup_only_prints_plan(tmp_path):
    script = Path(__file__).parents[1] / "scripts/setup_voice.py"
    assets = tmp_path / "not-created"
    result = subprocess.run([sys.executable, str(script), "--assets-dir", str(assets)],
                            capture_output=True, text=True, check=True)
    plan = json.loads(result.stdout)
    assert plan["voice_id"] == "bm_george"
    assert plan["download_requested"] is False
    assert not assets.exists()


def test_model_verification_rejects_truncated_or_changed_files(tmp_path, setup_module):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"expected")
    digest = hashlib.sha256(b"expected").hexdigest()
    assert setup_module.verify_file(model, 8, digest)
    assert not setup_module.verify_file(model, 9, digest)
    assert not setup_module.verify_file(model, 8, "0" * 64)


def test_bad_download_preserves_existing_file_and_removes_partial(tmp_path, setup_module, monkeypatch):
    model = tmp_path / "models/model.onnx"
    model.parent.mkdir()
    model.write_bytes(b"existing")
    monkeypatch.setattr(setup_module, "MODELS", {"model.onnx": (8, hashlib.sha256(b"expected").hexdigest())})
    monkeypatch.setattr(setup_module.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"truncated"))
    with pytest.raises(ValueError, match="verification"):
        setup_module.download_models(tmp_path)
    assert model.read_bytes() == b"existing"
    assert list(model.parent.iterdir()) == [model]


def test_verified_assets_are_reused_without_network(tmp_path, setup_module, monkeypatch):
    model = tmp_path / "models/model.onnx"
    model.parent.mkdir()
    model.write_bytes(b"expected")
    monkeypatch.setattr(setup_module, "MODELS", {"model.onnx": (8, hashlib.sha256(b"expected").hexdigest())})
    def no_network(*args, **kwargs):
        raise AssertionError("A verified model must not be downloaded again")
    monkeypatch.setattr(setup_module.urllib.request, "urlopen", no_network)
    setup_module.download_models(tmp_path)


def test_configuration_preserves_private_settings_and_requires_existing_home(tmp_path, setup_module):
    with pytest.raises(FileNotFoundError):
        setup_module.configure_voice(tmp_path / "missing", {"enabled": True})
    home = tmp_path / "private"
    home.mkdir()
    (home / "personal.json").write_text(json.dumps({"owner_id": "42", "voice": {"old": True}, "other": "keep"}))
    settings = {"enabled": True, "assets_dir": str(tmp_path / "assets"), "voice_id": "bm_george"}
    setup_module.configure_voice(home, settings)
    personal = json.loads((home / "personal.json").read_text())
    assert personal == {"owner_id": "42", "other": "keep", "voice": settings}
    assert json.loads((home / "voice/preferences.json").read_text())["enabled"]

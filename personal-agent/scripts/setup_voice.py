"""Optional local Kokoro setup. No arguments prints a plan without changing files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import uuid
import venv

PERSONAL = Path(__file__).resolve().parents[1]
REPOSITORY = PERSONAL.parent
sys.path.insert(0, str(PERSONAL))
from hermes_personal.voice import atomic_json, voice_assets

# Exact upstream model-files-v1.1 release files. Kept separate from the MIT code.
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODELS = {
    "kokoro-v1.0.onnx": (325505369, "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"),
    "voices-v1.0.bin": (28214398, "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
PACKAGES = ["kokoro-onnx==0.6.1", "onnxruntime==1.23.2", "soundfile==0.13.1"]


def verify_file(path, size, digest):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def download_models(assets):
    models = assets / "models"
    models.mkdir(parents=True, exist_ok=True)
    for name, (size, digest) in MODELS.items():
        destination = models / name
        if verify_file(destination, size, digest):
            continue
        temporary = models / (name + "." + uuid.uuid4().hex + ".part")
        try:
            print("Downloading " + name, file=sys.stderr, flush=True)
            with urllib.request.urlopen(RELEASE + "/" + name, timeout=90) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if not verify_file(temporary, size, digest):
                raise ValueError("Model failed size/SHA256 verification: " + name)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def install_environment(assets):
    if os.name != "nt":
        raise RuntimeError("The voice companion requires Windows")
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError("Use Python 3.11, 3.12 or 3.13 for voice setup (3.12 recommended)")
    environment = assets / "env"
    interpreter = environment / "Scripts/python.exe"
    if not interpreter.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    subprocess.run([str(interpreter), "-m", "pip", "install", *PACKAGES], check=True, stdout=sys.stderr)


def validate_install(settings):
    assets = voice_assets(settings)
    for name, (size, digest) in MODELS.items():
        if not verify_file(assets["assets_dir"] / "models" / name, size, digest):
            raise ValueError("Missing or invalid model: " + name + "; run with --download")
    # Check the dedicated environment and custom array; no inference or playback.
    interpreter = assets["assets_dir"] / "env/Scripts/python.exe"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "import tkinter, numpy, onnxruntime, soundfile, kokoro_onnx; "
        "from hermes_personal.voice_worker import load_voice_style; "
        "load_voice_style(sys.argv[2] or None); "
        "voices=numpy.load(sys.argv[3], allow_pickle=False); "
        "assert sys.argv[2] or sys.argv[4] in voices.files, 'Unknown built-in voice ID'"
    )
    subprocess.run([str(interpreter), "-c", code, str(PERSONAL), str(assets["voice_file"] or ""),
                    str(assets["assets_dir"] / "models/voices-v1.0.bin"), assets["voice_id"]], check=True)
    return assets


def configure_voice(home, settings):
    """Explicit opt-in to the caller's existing private Mika installation."""
    home = Path(home).expanduser().resolve()
    path = home / "personal.json"
    if not path.is_file():
        raise FileNotFoundError("Install Mika first, or pass --home pointing to its private home")
    personal = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(personal, dict) or not personal.get("owner_id"):
        raise ValueError("Expected an existing Mika personal.json with an owner_id")
    shutil.copytree(PERSONAL / "hermes_personal", home / "plugins/personal-assistant",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    personal["voice"] = settings
    atomic_json(path, personal)
    atomic_json(home / "voice/preferences.json", {"enabled": True})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets-dir", type=Path, default=REPOSITORY / ".local/voice")
    parser.add_argument("--voice-id", default="bm_george", help="Public English Kokoro voice, e.g. bm_george or af_heart")
    parser.add_argument("--voice-file", type=Path, help="Optional path to your own compatible numeric .npy style")
    parser.add_argument("--download", action="store_true", help="Create a voice venv, install dependencies and fetch verified public models")
    parser.add_argument("--configure", action="store_true", help="Enable voice in your existing private Mika home")
    parser.add_argument("--verify", action="store_true", help="Validate existing assets and dependencies without speaking")
    parser.add_argument("--home", type=Path, help="Private Mika home (defaults to your local deployment.json)")
    args = parser.parse_args(argv)
    assets = args.assets_dir.expanduser().resolve()
    settings = {"enabled": True, "assets_dir": str(assets), "voice_id": args.voice_id}
    if args.voice_file:
        style = args.voice_file.expanduser().resolve()
        if style.suffix.lower() != ".npy" or not style.is_file():
            parser.error("--voice-file must point to your existing .npy file")
        settings["voice_file"] = str(style)
    if not args.voice_id.startswith(("af_", "am_", "bf_", "bm_")):
        parser.error("The companion currently supports English voice IDs: af_, am_, bf_ or bm_")
    home = args.home
    if args.configure and home is None:
        deployment = PERSONAL / "deployment.json"
        if not deployment.is_file():
            parser.error("Install Mika first, or provide --home for your private installation")
        home = Path(json.loads(deployment.read_text(encoding="utf-8"))["home"])
    if args.configure and not (home.expanduser() / "personal.json").is_file():
        parser.error("--home must contain an existing Mika personal.json")
    if args.download:
        install_environment(assets)
        download_models(assets)
    if args.download or args.verify or args.configure:
        validate_install(settings)
    if args.configure:
        configure_voice(home, settings)
    print(json.dumps({"assets_dir": str(assets), "voice_id": args.voice_id,
                      "voice_file": settings.get("voice_file"), "download_requested": args.download,
                      "configured": args.configure, "models": {name: RELEASE + "/" + name for name in MODELS},
                      "next": "Restart Mika when idle." if args.configure else "Use --download --configure to opt in, or --verify to check existing files."}, indent=2))


if __name__ == "__main__":
    main()

# Optional local voice

Mika works with text only. Voice is an explicit opt-in on Windows, using a separate
Kokoro ONNX environment under `.local/voice`. No private voice, embedding, recording,
model weight or Python environment is included in this repository. The default
`bm_george` is a public Kokoro British English voice, not the author's personal voice.

Install Mika and configure your own Telegram bot and AI provider first. Use Windows
Python 3.11–3.13; Python 3.12 is recommended. Run these commands from the repository
root in PowerShell:

```powershell
# Prints the plan only: no download, installation, settings change or playback.
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py

# Explicitly install a dedicated voice environment and download public weights,
# validate them, then enable voice in your existing private Mika configuration.
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --download --configure
```

The download is about 354 MB in decimal units, plus Python packages. It uses CPU
inference; no GPU, speech API account, PyTorch or separate voice server is required.
The script checks exact byte sizes and SHA256 before accepting model files. Partial
downloads are removed and complete verified files are reused. Dependencies are
installed only in `.local/voice/env`. Keep the Python installation that created that
venv available. `kokoro-onnx` includes `espeakng-loader` for phonemization.

`--configure` uses the local `personal-agent/deployment.json` produced by Mika's
installer. For a different private installation add `--home C:\Your\MikaHome`.
It updates that home's `personal.json` voice section, copies the plugin code, and
enables its private `voice/preferences.json`. Restart Mika when its current task is
idle. Unrelated personal settings, provider credentials and SOUL are preserved.
`install_voice.py` is an alias for the same setup command.

You can split installation from enabling voice, or use a directory outside the checkout:

```powershell
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --download --assets-dir "$env:LOCALAPPDATA\MikaVoice"
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --verify --assets-dir "$env:LOCALAPPDATA\MikaVoice"
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --configure --assets-dir "$env:LOCALAPPDATA\MikaVoice" --voice-id af_heart
```

Use the same `--assets-dir` when verifying, configuring or running the optional
acceptance probe. Public English IDs beginning `af_`, `am_`, `bf_`, or `bm_` are
supported; American IDs use `en-us`, British IDs use `en-gb`. See the upstream
[voice list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md).
Unknown IDs fail validation before configuration is changed.

## Your own optional style

If you already have a compatible style you own or have permission to use, pass its
path explicitly. This setup does not clone voices or create embeddings:

```powershell
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --configure --voice-file "C:\MyPrivateVoice\own-style.npy"
```

The `.npy` must contain a finite numeric array of shape `(510, 1, 256)`, loaded with
`allow_pickle=False`. The private configuration stores the path. Setup never copies
or uploads your style. Keep it outside the checkout or inside ignored `.local/voice`.
`--voice-id` still chooses the English pronunciation locale when a custom style is used.
Run `--configure --voice-id bm_george` without `--voice-file` to return to a built-in
voice. Do not commit personal recordings or embeddings.

## What you hear and see

Mika speaks one initial acknowledgement and the final opening summary plus closing
questions. Detailed reports, code and URLs stay in the written chat. Selected spoken
sentences play through to their natural ending, without pitch, speed or timbre effects.
Playback keeps the existing peak normalization. A transparent, click-through desktop
orb is visible only during playback and does not take keyboard focus. A new private
owner message or `/stop` cancels speech. `/voice off` mutes it and `/voice on` enables
it again. Speech failures leave written replies available.

The voice worker runs locally after setup with Hugging Face offline mode enabled;
gateway API keys and Telegram tokens are excluded from its environment. Its mailbox,
status, short-lived WAV files and log are in your private Mika home under `voice/`.
Delete that folder's generated audio/log files when you no longer need them.

To explicitly play a local acceptance demonstration after setup (no Telegram message
or AI API request), run:

```powershell
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\voice_probe.py
```

The probe writes its screenshots/report under ignored `workspace/voice-validation`.

## Public asset sources and licenses

These are the exact files linked by the maintained
[kokoro-onnx project](https://github.com/thewh1teagle/kokoro-onnx):

| File | Direct upstream release URL | Bytes | SHA256 |
| --- | --- | ---: | --- |
| `kokoro-v1.0.onnx` | [Download](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx) | 325505369 | `beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a` |
| `voices-v1.0.bin` | [Download](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin) | 28214398 | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` |

The upstream project licenses `kokoro-onnx` code under MIT and the Kokoro model under
Apache 2.0. The [original Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M)
provides its model license and training-data attribution. Those licenses and notices
apply separately from Mika's source-code license. Custom styles retain their own
rights and terms. This repository distributes download instructions and setup code;
it does not redistribute those model files.

## Troubleshooting

Run `setup_voice.py --verify` with your configured asset/style arguments to check
the environment, model hashes, voice ID and style shape without playing audio.
For missing or invalid models rerun `--download`; a bad existing file is replaced
only after a fresh download passes verification. For worker errors inspect
`voice/worker.log` and `voice/status.json` in your private Mika home. Keep those logs
private, since they may include local paths or spoken text. If no orb appears, use a
logged-in interactive Windows desktop and confirm the default audio output works.

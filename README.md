# Mika Windows Assistant

A personal Telegram assistant for Windows, with optional local Kokoro speech
and a glowing desktop orb. Powered by [Nous Hermes Agent](https://github.com/NousResearch/hermes-agent).

**Bring your own Telegram bot, cloud AI key or local AI model, and voice setup.**
No author's tokens, chats, recordings, custom voice embedding, browser sessions,
runtime settings, or model weights are included.

## Requirements

- Windows 10/11, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Your own Telegram bot token and numeric Telegram user ID.
- An OpenAI-compatible cloud endpoint/key, or a local model server.
- A tool-capable model for agent actions. Screenshot understanding requires
  a vision-capable model; text-only local models have limited desktop support.
- Optional speakers and local Kokoro assets for voice.

Telegram still needs the internet when AI and speech run locally. Cloud models
receive prompts and observations sent to them. See [SECURITY.md](SECURITY.md).

## 1. Create your own configuration

```powershell
git clone https://github.com/ReamyNadzri/mika-windows-assistant.git
cd mika-windows-assistant
Copy-Item .env.example .env
notepad .env
```

Create a bot with the official [@BotFather](https://t.me/BotFather), using
`/newbot`. Put its token in your local `.env`, never a commit. Telegram's
[official tutorial](https://core.telegram.org/bots/tutorial) explains bot creation.

Send `/start` to **your new bot** in a private chat. Discover your numeric ID
before starting Mika:

```powershell
uv run --project personal-agent python personal-agent/scripts/check_telegram.py
```

Put your ID in `TELEGRAM_USER_ID`. Also set `OWNER_NAME`, `TIMEZONE`, and AI
settings. The check reads updates but sends no messages. Stop any other poller
using that bot while discovering your ID.

### Cloud AI

Use `.env.example`. Set `AI_MODE=cloud`, your provider's HTTPS `AI_BASE_URL`,
`AI_MODEL`, and **your own** `AI_API_KEY`. Enable `AI_SUPPORTS_VISION=true` only
for models that accept images. Providers must support OpenAI-compatible chat
completions; not every provider supports every agent capability.

### Local AI

Copy `.env.local.example` to `.env` instead. Install/start your local server,
load a tool-capable model, and set its installed name in `AI_MODEL`.
[Ollama](https://docs.ollama.com/api/openai-compatibility) normally uses
`http://localhost:11434/v1`; LM Studio commonly uses `http://localhost:1234/v1`.
Use the address your server actually displays.

`AI_MODE=local` accepts loopback endpoints only. An unauthenticated local server
does not require a cloud AI key; authenticated local servers can use their own
key in `AI_API_KEY`. No AI weights are bundled or automatically pulled. Speed,
tool calling, and image support depend on the model and your hardware.

## 2. Install and configure

Run from this repository's root in PowerShell:

```powershell
uv python install 3.12
uv sync --project personal-agent --python 3.12
.\personal-agent\scripts\Setup.ps1 -Install
```

Setup validates `.env`, installs the pinned official Hermes runtime (`v0.22.2`,
commit `d923a5eb487a17c0a68801bba9ef0f56c81040a8`), and creates private settings
in your Windows local application-data directory. Use `-HermesHome` for a custom
private location. `personal-agent/deployment.json` records your local install
and is ignored by Git. If your PowerShell policy blocks a downloaded script,
review it and use your normal approved method for running local scripts.

## 3. Start Mika

Double-click **Start-Mika.cmd**, or run:

```powershell
.\personal-agent\scripts\Manage.ps1 Start
.\personal-agent\scripts\Manage.ps1 Status
```

Then message **your own bot** in Telegram. Keep the PC awake and signed in for
desktop control and voice. `/stop` cancels a task, `/new` starts a new chat, and
`/jobs` shows recent work. **Stop-Mika.cmd** stops the gateway.

Only your configured Telegram user in a private chat can request work. Approval
checks are on by default. `TRUSTED_OWNER=true` deliberately permits broader
owner-requested PC actions; understand that choice before enabling it. Mika
does not bypass account logins, CAPTCHA, UAC, or a locked Windows session.

## Optional voice and orb

Get text chat working first, then install your own local voice:

```powershell
.\personal-agent\.venv\Scripts\python.exe .\personal-agent\scripts\setup_voice.py --download --configure
.\personal-agent\scripts\Manage.ps1 Restart
```

This creates an isolated voice environment and downloads Kokoro assets under
ignored `.local/voice`. A public built-in voice is the default. Choose another
preset or provide a custom `.npy` style you have permission to use. The author's
custom voice is not included. See [voice setup](docs/voice.md).

Mika speaks the first acknowledgement, the important final summary, and any
closing question. Details stay in chat. Selected sentences finish naturally,
with a cyan orb during playback. New messages and `/stop` interrupt old speech.
`/voice off` mutes; `/voice on` enables speech; `/voice` shows its state.

## Optional Windows sign-in startup

```powershell
.\personal-agent\scripts\Manage.ps1 InstallStartup
.\personal-agent\scripts\Manage.ps1 Status
```

Hermes uses a Windows login task when available, or a hidden Startup-folder
launcher without administrator approval. Desktop voice needs an interactive
session, so this runs **at sign-in**, not before login. Remove automatic startup
with `Manage.ps1 RemoveStartup`.

## Verification and troubleshooting

```powershell
uv run --project personal-agent pytest -q personal-agent/tests
.\personal-agent\scripts\Manage.ps1 Doctor
```

- No bot reply: check your token/user ID and model server, and ensure only one
  gateway is polling the bot.
- No speech: complete voice setup, try `/voice on`, and check the default
  Windows output device and volume.
- Local model issues: tool calling and image understanding are separate model
  capabilities. Use a model appropriate for the requested actions and hardware.
- Runtime missing: rerun Setup with `-Install`. Private logs stay in the runtime
  home; redact them before posting an issue.

Portable configuration and speech routing have offline regression coverage.
Fresh Windows installs and each user's cloud/local models need their own
end-to-end checks. No arbitrary model or provider is guaranteed to work.

The plugin is in `personal-agent/hermes_personal/`, setup/management scripts in
`personal-agent/scripts/`, and tests in `personal-agent/tests/`. Hermes is
installed separately. Kokoro code, weights, and voice data retain upstream
terms, linked in the voice guide. This repository contains no movie audio and
is not an official Iron Man/JARVIS product.

No redistribution license for this repository's original code has been selected
yet; public visibility alone does not grant a software license.

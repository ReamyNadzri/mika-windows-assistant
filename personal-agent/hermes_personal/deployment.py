"""Repeatable local configuration; secrets never enter command arguments or YAML."""
from __future__ import annotations

import json
import ipaddress
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def read_env(path):
    """Read single-line literal values; never expand variables or evaluate content."""
    result = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Environment line {number} must contain KEY=value on one line")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or key in result:
            raise ValueError(f"Environment line {number} has an invalid or duplicate setting name")
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"Environment line {number} must close its quote on the same line")
            value = value[1:-1]
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError(f"Environment line {number} contains a control character")
        result[key] = value
    return result


def default_home():
    """Keep private runtime files under the current Windows account."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "HermesPersonal"


def _boolean(values, key, default):
    value = values.get(key, str(default)).lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{key} must be true or false")
    return value == "true"


def validate_settings(source_env):
    """Validate everything before setup creates or changes runtime files."""
    values = read_env(source_env)
    mode = values.get("AI_MODE", "cloud").lower()
    if mode not in {"cloud", "local"}:
        raise ValueError("AI_MODE must be cloud or local")
    base = values.get("AI_BASE_URL", values.get("DEEPSEEK_BASE_URL", ""))
    model = values.get("AI_MODEL", values.get("DEEPSEEK_MODEL", ""))
    # A local setup must never inherit an old cloud key accidentally.
    key = values.get("AI_API_KEY", values.get("DEEPSEEK_API_KEY", "") if mode == "cloud" else "")
    owner, token = values.get("TELEGRAM_USER_ID", ""), values.get("TELEGRAM_BOT_TOKEN", "")
    if not re.fullmatch(r"[0-9]+", owner) or int(owner) <= 0:
        raise ValueError("TELEGRAM_USER_ID must be a positive numeric ID")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("TELEGRAM_BOT_TOKEN must be your own BotFather token")
    if not model or any(character.isspace() for character in model):
        raise ValueError("AI_MODEL must contain your provider's model ID without whitespace")
    placeholder = re.sub(r"[^a-z0-9]", "", key.lower())
    if mode == "cloud" and (not key or placeholder in {
        "local", "none", "null", "placeholder", "changeme", "replaceme", "yourkey", "yourapikey",
        "apikey", "pastekeyhere", "pasteyourkeyhere", "skyourkeyhere", "skyourapikeyhere",
    } or placeholder.startswith(("your", "pasteyour", "replacewith", "skyour")) or key.startswith(("<", "${", "{{"))):
        raise ValueError("AI_API_KEY must be your own cloud provider key, not an example placeholder")
    if any(character.isspace() for character in key):
        raise ValueError("AI_API_KEY must be a single value without whitespace")
    try:
        endpoint = urlsplit(base)
        host, port = endpoint.hostname, endpoint.port
    except ValueError:
        raise ValueError("AI_BASE_URL must be a valid OpenAI-compatible base URL") from None
    if (not host or endpoint.scheme not in {"http", "https"} or endpoint.username is not None
            or endpoint.password is not None or "?" in base or "#" in base or "\\" in base
            or any(character.isspace() for character in base)
            or re.search(r"%(?:00|09|0a|0d)", base, re.IGNORECASE)
            or endpoint.netloc.endswith(":") or port == 0):
        raise ValueError("AI_BASE_URL must have no credentials, query, fragment, whitespace or invalid port")
    if mode == "cloud" and endpoint.scheme != "https":
        raise ValueError("Cloud AI_BASE_URL requires HTTPS")
    if mode == "local" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Local AI_BASE_URL must use localhost, 127.0.0.1 or [::1]")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host) or ".." in host:
            raise ValueError("AI_BASE_URL has an invalid hostname") from None
    owner_name = values.get("OWNER_NAME", "Owner")
    if (not 1 <= len(owner_name) <= 80 or not any(character.isalnum() for character in owner_name)
            or any(not character.isalnum() and character not in " .'-" for character in owner_name)):
        raise ValueError("OWNER_NAME must be a short name using letters, numbers, spaces, apostrophes, periods or hyphens")
    timezone = values.get("TIMEZONE", "UTC")
    # Windows Python may not include the optional IANA tzdata package. Validate the
    # identifier's syntax here; the installed Hermes runtime resolves its database.
    if not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z0-9_+-]+)*", timezone):
        raise ValueError("TIMEZONE must be an IANA name such as UTC or Europe/London")
    return {"mode": mode, "base": base, "model": model, "key": key or "local", "owner": owner,
            "token": token, "owner_name": owner_name, "timezone": timezone,
            "supports_vision": _boolean(values, "AI_SUPPORTS_VISION", mode == "cloud"),
            "trusted_owner": _boolean(values, "TRUSTED_OWNER", False)}


def configure(home, source_env, project, *, plugin_source=None):
    home, project = Path(home).resolve(), Path(project).resolve()
    settings = validate_settings(source_env)
    owner, base, model = settings["owner"], settings["base"], settings["model"]
    supports_vision = settings["supports_vision"]
    personal_path, config_path = home / "personal.json", home / "config.yaml"
    previous = json.loads(personal_path.read_text(encoding="utf-8")) if personal_path.is_file() else {}
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    config = config if isinstance(config, dict) else {}
    for path in (("agent",), ("platforms",), ("platforms", "telegram"), ("platforms", "telegram", "extra")):
        section = config
        for part in path:
            section = section.get(part, {})
            if not isinstance(section, dict):
                raise ValueError("Existing config.yaml section " + ".".join(path) + " must be a mapping")
    plugin_source = Path(plugin_source or Path(__file__).parent)
    if not plugin_source.is_dir():
        raise ValueError("The personal assistant plugin source directory is missing")
    soul_source = Path(__file__).parents[1] / "config" / "SOUL.md"
    soul = soul_source.read_text(encoding="utf-8") if soul_source.is_file() else None
    if soul is not None:
        for name, value in {"OWNER_NAME": settings["owner_name"], "TIMEZONE": settings["timezone"], "ASSISTANT_NAME": "Mika"}.items():
            soul = soul.replace("{{" + name + "}}", value)
    workspace = project / "workspace"
    secret_values = {"TELEGRAM_BOT_TOKEN": settings["token"], "TELEGRAM_ALLOWED_USERS": owner, "TELEGRAM_HOME_CHANNEL": owner, "OPENAI_API_KEY": settings["key"], "OPENAI_BASE_URL": base, "HERMES_TIMEZONE": settings["timezone"]}
    env_file = home / ".env"
    env_text = "\n".join(f"{k}={v}" for k, v in secret_values.items()) + "\n"
    previous_env = env_file.read_text(encoding="utf-8") if env_file.is_file() else None
    user_home = Path.home()
    roots = [str(workspace), str(project), *(str((user_home / folder).resolve()) for folder in ("Desktop", "Documents", "Downloads"))]
    personal = {"owner_id": owner, "owner_name": settings["owner_name"], "assistant_name":"Mika", "trusted_owner":settings["trusted_owner"], "visible_actions":True, "fast_visual":True, "model": model, "ai_mode": settings["mode"], "supports_vision": supports_vision, "allowed_roots": roots, "protected_paths": [str(project / "personal-agent")]}
    if isinstance(previous, dict) and isinstance(previous.get("voice"), dict):
        personal["voice"] = previous["voice"]
    toolsets = ["personal_assistant", "web", "browser", "vision", "computer_use", "memory", "session_search", "skills", "terminal", "code_execution"]
    if not supports_vision:
        toolsets.remove("vision")
    config.update({
        "model": {"provider": "custom", "default": model, "base_url": base, "api_mode": "chat_completions", "context_length": 65536, "reasoning_echo": True, "supports_vision":supports_vision},
        "providers": {"custom":{"request_timeout_seconds":120,"stale_timeout_seconds":120}},
        "fallback_providers": [],
        "toolsets": toolsets,
        "tool_search": {"enabled":"off"},
        "platform_toolsets": {"telegram": toolsets, "cli": toolsets, "cron": [], "api_server": []},
        "terminal": {"backend": "local", "cwd": str(workspace), "timeout": 120},
        "approvals": {"mode": "off" if settings["trusted_owner"] else "manual", "timeout": 300, "cron_mode": "deny", "single_query_mode": "deny", "unattended_mode": "deny", "mcp_reload_confirm": True, "destructive_slash_confirm": True},
        "browser": {"backend": "off", "cloud_provider": "local", "cdp_url": "http://127.0.0.1:9223", "use_real_profile": False, "real_profile_autoclose": False, "headed": True, "restrict_evaluate": False, "allow_unsafe_evaluate": True, "inactivity_timeout": 600},
        "computer_use": {"permission_mode": "standard", "autostart": False, "cua_telemetry": False, "no_overlay":False},
        "web": {"search_backend": "ddgs"},
        "plugins": {"enabled": ["personal-assistant"], "hook_callback_timeout": 25},
        "auxiliary": {"vision": {"provider": "main", "model": model, "base_url": base}, "compression": {"provider": "main", "model": model, "base_url": base, "timeout": 120}, "background_review": {"enabled": False}, "title_generation": {"enabled": False}},
        "stt": {"enabled": False},
        "human_delay": {"mode": "off"},
        "max_concurrent_sessions": 1,
    })
    config.setdefault("agent", {}).update({"max_iterations": 40, "api_max_retries":2, "gateway_startup_warmup_timeout": 30, "gateway_auto_continue_freshness": 1})
    if supports_vision:
        config["agent"]["image_input_mode"] = "native"
    else:
        config["agent"].pop("image_input_mode", None)
    # The gateway handles only Telegram; no remote webhooks or public API listener.
    config.setdefault("platforms", {}).setdefault("telegram", {}).setdefault("extra", {}).update({"drop_pending_on_cold_boot": False, "max_concurrent_updates": 4, "command_menu":{"max_commands":4,"priority_mode":"replace","priority":["stop","new","status","jobs"]}})
    config_text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    personal_text = json.dumps(personal, indent=2)
    # Only write after user settings, existing structures and templates validate.
    home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(exist_ok=True)
    for folder in ("inbox", "outputs", "browser-profile", "logs", "plugins", "cache"):
        (home / folder).mkdir(exist_ok=True)
    # Exclusive local backups; no credentials are logged or written to source files.
    if previous_env is not None and previous_env != env_text:
        shutil.copy2(env_file, home / ".env.previous")
    env_file.write_text(env_text, encoding="utf-8")
    personal_path.write_text(personal_text, encoding="utf-8")
    config_path.write_text(config_text, encoding="utf-8")
    destination = home / "plugins" / "personal-assistant"
    shutil.copytree(plugin_source, destination, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if soul is not None:
        (home / "SOUL.md").write_text(soul, encoding="utf-8")
    record = {"home": str(home), "project": str(project), "upstream_version": "v0.22.2", "launcher": str(home / "bin" / "hermes.cmd"), "browser_port": 9223}
    (project / "personal-agent" / "deployment.json").parent.mkdir(parents=True, exist_ok=True)
    (project / "personal-agent" / "deployment.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def configure_voice(home, assets_dir, *, python=None, voice_id="bm_george", voice_file=None,
                    plugin_source=None, soul_source=None):
    """Opt in using public Kokoro assets and an optional caller-owned style path."""
    from .voice import atomic_json, voice_assets

    settings = {"enabled": True, "assets_dir": str(Path(assets_dir).expanduser().resolve()), "voice_id": voice_id}
    if python is not None:
        settings["python"] = str(Path(python).expanduser().resolve())
    if voice_file is not None:
        settings["voice_file"] = str(Path(voice_file).expanduser().resolve())
    voice_assets(settings)
    home = Path(home).expanduser().resolve()
    path = home / "personal.json"
    personal = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(personal, dict) or not personal.get("owner_id"):
        raise ValueError("Expected an existing Mika personal.json with an owner_id")
    plugin_source = Path(plugin_source or Path(__file__).parent)
    shutil.copytree(plugin_source, home / "plugins/personal-assistant", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if soul_source is not None and Path(soul_source).is_file():
        shutil.copy2(soul_source, home / "SOUL.md")
    personal["voice"] = settings
    atomic_json(path, personal)
    atomic_json(home / "voice/preferences.json", {"enabled": True})
    return personal["voice"]

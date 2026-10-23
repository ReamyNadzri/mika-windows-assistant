import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hermes_personal.deployment import configure, configure_voice


def test_configuration_reuses_credentials_without_putting_keys_in_yaml(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "settings.env"
    source.write_text("TELEGRAM_BOT_TOKEN=123:private\nTELEGRAM_USER_ID=42\nDEEPSEEK_API_KEY=secret-value\nDEEPSEEK_BASE_URL=https://example.com/v1\nDEEPSEEK_MODEL=vision-model\nHERMES_PIN_HASH=old-unused\n")
    home = tmp_path / "runtime"
    configure(home, source, project, plugin_source=Path(__file__).parents[1] / "hermes_personal")
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["model"]["provider"] == "custom"
    assert config["model"]["default"] == "vision-model"
    assert config["approvals"]["mode"] == "manual"
    assert config["browser"]["headed"] is True
    assert config["computer_use"]["no_overlay"] is False
    assert config["browser"]["use_real_profile"] is False
    assert "secret-value" not in (home / "config.yaml").read_text()
    assert "secret-value" in (home / ".env").read_text()
    assert "TELEGRAM_ALLOWED_USERS=42" in (home / ".env").read_text()
    assert "old-unused" not in (home / ".env").read_text()
    assert (home / "plugins" / "personal-assistant" / "plugin.yaml").is_file()
    personal = json.loads((home / "personal.json").read_text())
    assert personal["owner_id"] == "42"
    assert personal["assistant_name"] == "Mika" and not personal["trusted_owner"] and personal["visible_actions"]
    assert personal["fast_visual"] is True


def test_configuration_refuses_invalid_or_missing_owner_and_non_https_cloud(tmp_path):
    source = tmp_path / "source.env"
    source.write_text("TELEGRAM_BOT_TOKEN=123:x\nTELEGRAM_USER_ID=not-a-number\nDEEPSEEK_API_KEY=x\nDEEPSEEK_MODEL=x\nDEEPSEEK_BASE_URL=http://remote.example/v1\n")
    with pytest.raises(ValueError):
        configure(tmp_path / "runtime", source, tmp_path)
    assert not (tmp_path / "runtime" / ".env").exists()


def test_configuration_refresh_preserves_the_installed_voice(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "settings.env"
    source.write_text("TELEGRAM_BOT_TOKEN=123:private\nTELEGRAM_USER_ID=42\nDEEPSEEK_API_KEY=secret-value\nDEEPSEEK_BASE_URL=https://example.com/v1\nDEEPSEEK_MODEL=vision-model\n")
    home = tmp_path / "runtime"
    configure(home, source, project)
    personal = json.loads((home / "personal.json").read_text())
    personal["voice"] = {"enabled": True, "studio": "D:/VoiceStudio"}
    (home / "personal.json").write_text(json.dumps(personal))
    configure(home, source, project)
    assert json.loads((home / "personal.json").read_text())["voice"] == {"enabled": True, "studio": "D:/VoiceStudio"}


def test_explicit_voice_install_keeps_other_live_settings_and_credentials(tmp_path):
    home, studio, plugin = tmp_path / "home", tmp_path / "studio", tmp_path / "plugin"
    home.mkdir()
    plugin.mkdir()
    (plugin / "plugin.py").write_text("example plugin")
    soul = tmp_path / "SOUL.md"
    soul.write_text("A concise conversational lead.")
    original = {"owner_id": "42", "model": "unchanged", "allowed_roots": ["D:/work"]}
    (home / "personal.json").write_text(json.dumps(original))
    (home / ".env").write_text("PRIVATE_KEY=kept-local\n")
    (home / "config.yaml").write_text("unchanged: true\n")
    for asset in ("env/Scripts/pythonw.exe", "models/kokoro-v1.0.onnx", "models/voices-v1.0.bin"):
        path = studio / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    configure_voice(home, studio, plugin_source=plugin, soul_source=soul)
    result = json.loads((home / "personal.json").read_text())
    assert {k: result[k] for k in original} == original
    assert result["voice"]["enabled"]
    assert result["voice"]["voice_id"] == "bm_george"
    assert result["voice"]["assets_dir"] == str(studio.resolve())
    assert "voice_file" not in result["voice"]
    assert (home / ".env").read_text() == "PRIVATE_KEY=kept-local\n"
    assert (home / "config.yaml").read_text() == "unchanged: true\n"
    assert (home / "plugins/personal-assistant/plugin.py").read_text() == "example plugin"


def test_missing_voice_assets_do_not_change_live_configuration(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    path = home / "personal.json"
    path.write_text('{"owner_id":"42"}')
    original = path.read_bytes()
    with pytest.raises(FileNotFoundError):
        configure_voice(home, tmp_path / "missing")
    assert path.read_bytes() == original and not (home / "plugins").exists()


def write_settings(path, **overrides):
    values = {"AI_MODE": "cloud", "AI_BASE_URL": "https://api.example.com/v1",
              "AI_MODEL": "my-model", "AI_API_KEY": "private-test-key",
              "TELEGRAM_BOT_TOKEN": "123:private", "TELEGRAM_USER_ID": "42",
              "OWNER_NAME": "Alex", "TIMEZONE": "Europe/London"}
    values.update(overrides)
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items() if value is not None), encoding="utf-8")
    return path


@pytest.mark.parametrize("endpoint", ["http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://[::1]:8080/v1"])
def test_local_model_uses_loopback_and_optional_nonsecret_key(tmp_path, endpoint):
    source = write_settings(tmp_path / "settings.env", AI_MODE="local", AI_BASE_URL=endpoint, AI_API_KEY=None)
    home = tmp_path / "runtime"
    configure(home, source, tmp_path)
    config = yaml.safe_load((home / "config.yaml").read_text())
    personal = json.loads((home / "personal.json").read_text())
    assert config["model"]["base_url"] == endpoint
    assert config["model"]["supports_vision"] is False
    assert "image_input_mode" not in config["agent"]
    assert "vision" not in config["toolsets"]
    assert "OPENAI_API_KEY=local\n" in (home / ".env").read_text()
    assert "HERMES_TIMEZONE=Europe/London\n" in (home / ".env").read_text()
    assert personal["owner_name"] == "Alex" and personal["ai_mode"] == "local"
    assert personal["trusted_owner"] is False


def test_generic_cloud_fields_take_precedence_and_allow_explicit_options(tmp_path):
    source = write_settings(tmp_path / "settings.env", AI_SUPPORTS_VISION="false", TRUSTED_OWNER="true",
                            DEEPSEEK_API_KEY="legacy-unused-key", DEEPSEEK_BASE_URL="https://unused.example/v1",
                            DEEPSEEK_MODEL="unused-model")
    home = tmp_path / "runtime"
    configure(home, source, tmp_path)
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["model"]["default"] == "my-model"
    assert config["model"]["supports_vision"] is False
    assert json.loads((home / "personal.json").read_text())["trusted_owner"] is True
    assert "private-test-key" in (home / ".env").read_text()
    assert "private-test-key" not in (home / "config.yaml").read_text()
    assert "legacy-unused-key" not in (home / ".env").read_text()


def test_local_vision_can_be_enabled_explicitly(tmp_path):
    source = write_settings(tmp_path / "settings.env", AI_MODE="local", AI_BASE_URL="http://localhost:1234/v1",
                            AI_API_KEY=None, AI_SUPPORTS_VISION="true")
    home = tmp_path / "runtime"
    configure(home, source, tmp_path)
    config = yaml.safe_load((home / "config.yaml").read_text())
    assert config["model"]["supports_vision"] is True and "vision" in config["toolsets"]


@pytest.mark.parametrize("trusted, expected_mode", [("false", "manual"), ("true", "off")])
def test_native_approval_mode_matches_explicit_owner_trust(tmp_path, trusted, expected_mode):
    source = write_settings(tmp_path / "settings.env", TRUSTED_OWNER=trusted)
    home = tmp_path / "runtime"
    configure(home, source, tmp_path)
    config = yaml.safe_load((home / "config.yaml").read_text())
    personal = json.loads((home / "personal.json").read_text())
    assert personal["trusted_owner"] is (trusted == "true")
    assert config["approvals"]["mode"] == expected_mode
    assert config["approvals"]["cron_mode"] == "deny"
    assert config["approvals"]["unattended_mode"] == "deny"


def test_configuration_refresh_removes_previous_approval_bypass_without_trusted_opt_in(tmp_path):
    source = write_settings(tmp_path / "settings.env", TRUSTED_OWNER="true")
    home = tmp_path / "runtime"
    configure(home, source, tmp_path)
    assert yaml.safe_load((home / "config.yaml").read_text())["approvals"]["mode"] == "off"
    write_settings(source)
    configure(home, source, tmp_path)
    assert yaml.safe_load((home / "config.yaml").read_text())["approvals"]["mode"] == "manual"
    assert json.loads((home / "personal.json").read_text())["trusted_owner"] is False


@pytest.mark.parametrize("changes", [
    {"AI_MODE": "other"}, {"AI_API_KEY": None}, {"AI_API_KEY": "your-api-key"},
    {"AI_API_KEY": "local"}, {"AI_API_KEY": "<your-key>"},
    {"AI_API_KEY": "YOUR_PROVIDER_API_KEY"},
    {"AI_BASE_URL": "http://api.example.com/v1"},
    {"AI_BASE_URL": "https://name:password@api.example.com/v1"},
    {"AI_BASE_URL": "https://api.example.com/v1?api_key=secret"},
    {"AI_BASE_URL": "https://api.example.com/v1#fragment"},
    {"AI_BASE_URL": "https://api.example.com:invalid/v1"},
    {"AI_BASE_URL": "https://api.example.com/v1\\n"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://remote.example.com/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://127.0.0.2:8080/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://localhost.example.com:8080/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://127.1:8080/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://[::ffff:127.0.0.1]:8080/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://[::1]evil.example:8080/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://[::1]suffix/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "https://remote.example.com/v1"},
    {"AI_MODE": "local", "AI_BASE_URL": "http://localhost:0/v1"},
    {"TELEGRAM_USER_ID": "0"}, {"TELEGRAM_USER_ID": "not-an-id"},
    {"TELEGRAM_BOT_TOKEN": "paste-token-here"}, {"AI_MODEL": ""},
    {"AI_SUPPORTS_VISION": "maybe"}, {"TRUSTED_OWNER": "maybe"},
    {"TIMEZONE": "../private"}, {"OWNER_NAME": "Alex {{instructions}}"},
    {"OWNER_NAME": '"Alex\nnew instruction"'},
    {"AI_BASE_URL": '"https://api.example.com/v1\nnot-a-setting"'},
])
def test_invalid_settings_leave_existing_runtime_and_project_unchanged(tmp_path, changes):
    source = write_settings(tmp_path / "settings.env", **changes)
    home = tmp_path / "runtime"
    home.mkdir()
    (home / ".env").write_text("PREVIOUS=secret\n")
    (home / "personal.json").write_text('{"owner_id":"previous"}')
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        configure(home, source, tmp_path)
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not (tmp_path / "workspace").exists()


def test_soul_template_uses_validated_owner_and_timezone(tmp_path, monkeypatch):
    import hermes_personal.deployment as deployment
    source = write_settings(tmp_path / "settings.env", OWNER_NAME="Zoë O'Neil")
    package = tmp_path / "package"
    (package / "hermes_personal").mkdir(parents=True)
    (package / "config").mkdir()
    (package / "config/SOUL.md").write_text("{{ASSISTANT_NAME}} assists {{OWNER_NAME}} in {{TIMEZONE}}.", encoding="utf-8")
    monkeypatch.setattr(deployment, "__file__", str(package / "hermes_personal/deployment.py"))
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    home = tmp_path / "runtime"
    configure(home, source, tmp_path, plugin_source=plugin)
    assert (home / "SOUL.md").read_text(encoding="utf-8") == "Mika assists Zoë O'Neil in Europe/London."


def test_default_runtime_home_uses_current_windows_account(tmp_path, monkeypatch):
    from hermes_personal.deployment import default_home
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "DifferentAccount/Local"))
    assert default_home() == tmp_path / "DifferentAccount/Local/HermesPersonal"


@pytest.mark.parametrize("config", ["agent: []\n", "platforms: []\n", "platforms:\n  telegram: []\n", "platforms:\n  telegram:\n    extra: []\n"])
def test_malformed_existing_runtime_settings_fail_before_any_writes(tmp_path, config):
    source = write_settings(tmp_path / "settings.env")
    home = tmp_path / "runtime"
    home.mkdir()
    (home / "config.yaml").write_text(config)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        configure(home, source, tmp_path)
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not (home / "plugins").exists() and not (tmp_path / "workspace").exists()


def test_configuration_cli_uses_root_env_and_current_account_without_leaking_secrets(tmp_path):
    exported = Path(__file__).parents[1]
    project = tmp_path / "Different checkout"
    package = project / "personal-agent"
    (package / "scripts").mkdir(parents=True)
    shutil.copy2(exported / "scripts/configure.py", package / "scripts/configure.py")
    shutil.copytree(exported / "hermes_personal", package / "hermes_personal", ignore=shutil.ignore_patterns("__pycache__"))
    write_settings(project / ".env")
    environment = dict(os.environ, LOCALAPPDATA=str(tmp_path / "DifferentAccount/Local"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, str(package / "scripts/configure.py"), "--validate-only"],
                            env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True
    assert "private-test-key" not in result.stdout + result.stderr
    assert not (tmp_path / "DifferentAccount").exists() and not (project / "workspace").exists()
    result = subprocess.run([sys.executable, str(package / "scripts/configure.py")],
                            env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    runtime = tmp_path / "DifferentAccount/Local/HermesPersonal"
    assert json.loads(result.stdout)["home"] == str(runtime.resolve())
    record = json.loads((package / "deployment.json").read_text())
    assert record["project"] == str(project.resolve())
    assert "private-test-key" not in result.stdout + result.stderr

import json
import subprocess
from pathlib import Path

import pytest

from scripts import runtime


def managed_home(tmp_path, monkeypatch):
    home = tmp_path / "physical home"
    store = home / "tools"
    interpreter = store / "python-pinned" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    (store / "facts.json").write_text(json.dumps({"packages":{"python":{"entry":"python-pinned"}}}))
    (home / "hermes-agent").mkdir()
    monkeypatch.setattr(runtime,"home",home)
    monkeypatch.setattr(runtime,"record",{"launcher":str(tmp_path / "broken-old-launcher.cmd")})
    return home, interpreter


def test_runtime_resolution_uses_physical_committed_python_without_batch_launcher(tmp_path, monkeypatch):
    home, interpreter = managed_home(tmp_path,monkeypatch)
    def run(command, **kwargs):
        assert Path(command[0]) == interpreter
        assert command[1:3] == ["-I","-c"]
        assert "print_runtime_command" in command[3]
        assert not any(str(value).endswith(".cmd") for value in command)
        assert command[4:] == ["--","gateway","status"]
        return subprocess.CompletedProcess(command,0,json.dumps([str(interpreter),"-I","-c","official bootstrap","gateway","status"]),"")
    monkeypatch.setattr(runtime.subprocess,"run",run)
    command = runtime.resolve_runtime(["gateway","status"])
    assert command[0] == str(interpreter) and command[-2:] == ["gateway","status"]


def test_runtime_failure_explains_the_error_without_secret_or_traceback(tmp_path, monkeypatch, capsys):
    home, interpreter = managed_home(tmp_path,monkeypatch)
    (home / ".env").write_text("OPENAI_API_KEY=private-provider-value\nTELEGRAM_BOT_TOKEN=123:private-bot\n")
    monkeypatch.setattr(runtime.subprocess,"run",lambda *a,**kw:subprocess.CompletedProcess(a[0],1,"","Cannot start: private-provider-value and 123:private-bot"))
    assert runtime.main(["--","gateway","status"]) == 1
    output = capsys.readouterr().err
    assert "Cannot start" in output and "[redacted]" in output
    assert "private-provider-value" not in output and "123:private-bot" not in output
    assert "Traceback" not in output


def test_runtime_refuses_store_entries_escaping_owned_tools(tmp_path, monkeypatch):
    home, interpreter = managed_home(tmp_path,monkeypatch)
    (home / "tools" / "facts.json").write_text(json.dumps({"packages":{"python":{"entry":"../../other-python"}}}))
    with pytest.raises(RuntimeError,match="Python"):
        runtime.resolve_runtime()

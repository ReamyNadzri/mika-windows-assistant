import os

import pytest

from hermes_personal.files import PathPolicy, windows_reserved


@pytest.mark.parametrize("name", ["CON", "NUL.txt", "COM1", "LPT9.log", "COM¹.txt", "CONIN$", "CON .txt", "name.", "name ", "bad?name", "bad|name", "bad\x01name"])
def test_reserved_names_stay_blocked_without_python313_helper(name, monkeypatch):
    monkeypatch.delattr(os.path, "isreserved", raising=False)
    assert windows_reserved(name)


def test_ordinary_files_work_without_python313_helper(tmp_path, monkeypatch):
    monkeypatch.delattr(os.path, "isreserved", raising=False)
    assert not windows_reserved("report.txt")
    target = tmp_path / "report.txt"
    assert PathPolicy([tmp_path]).resolve(str(target)) == target.resolve()

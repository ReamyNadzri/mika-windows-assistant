import pytest
from scripts.startup_helpers import supervise_vbs


def test_hidden_launcher_waits_and_propagates_failure_for_scheduler_restart():
    source = 'Option Explicit\nDim sh\nsh.Run "python.exe -m hermes_cli.main gateway run", 0, False\n'
    result = supervise_vbs(source)
    assert 'personalExitCode = sh.Run("python.exe -m hermes_cli.main gateway run --external-supervisor", 0, True)' in result
    assert "WScript.Quit personalExitCode" in result
    assert "Dim personalExitCode" in result
    with pytest.raises(ValueError):
        supervise_vbs(source.replace("gateway run","different command"))

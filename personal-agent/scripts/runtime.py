"""Run scripts or Hermes commands using the exact published managed runtime."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
record = json.loads((root / "deployment.json").read_text()) if (root / "deployment.json").is_file() else {}
home = Path(record["home"]) if record.get("home") else None
environment = os.environ.copy()
if home:
    environment["HERMES_HOME"] = str(home)
environment["HERMES_NONINTERACTIVE"] = "1"
environment["PYTHONUTF8"] = "1"


def safe_error(message):
    """Launcher diagnostics must never reveal values from the private env file."""
    if home and (home / ".env").is_file():
        for line in (home / ".env").read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and any(word in key.upper() for word in ("KEY","TOKEN","PASSWORD","SECRET")):
                value = value.strip().strip('\"').strip("'")
                if value:
                    message = message.replace(value,"[redacted]")
    return message[-1800:]


def resolve_runtime(arguments=()):
    """Ask pinned Hermes directly using its committed physical store Python.

    The generated .cmd references the installer-visible AppData alias, which
    may exist only inside Codex's redirected filesystem. It is also a long
    inline batch command. Neither alias nor cmd.exe is needed for this API.
    """
    if home is None:
        raise RuntimeError("Mika is not configured. Run Setup.ps1 first.")
    store = (home / "tools").resolve()
    try:
        facts = json.loads((store / "facts.json").read_text(encoding="utf-8-sig"))
        interpreter = (store / facts["packages"]["python"]["entry"] / "python.exe").resolve()
        if not interpreter.is_relative_to(store) or not interpreter.is_file():
            raise ValueError("Python is outside the managed store or missing")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise RuntimeError("The managed Python runtime is missing. Repair the installation with Setup.ps1.") from exc
    source = str(home / "hermes-agent")
    query_code = "; ".join(["import sys", "from pathlib import Path", "sys.path.insert(0," + repr(source) + ")", "from hermes_cli._launchers import print_runtime_command", "print_runtime_command(Path(" + repr(source) + "),sys.argv[1:])"])
    query_env = environment.copy()
    query_env["HERMES_HOME"] = str(home)
    for key in ("HERMES_RUNTIME_DIR","PYTHONHOME","PYTHONPATH","VIRTUAL_ENV"):
        query_env.pop(key,None)
    query = subprocess.run([str(interpreter),"-I","-c",query_code,"--",*arguments], env=query_env, capture_output=True, text=True, timeout=30)
    if query.returncode:
        raise RuntimeError("Mika runtime launcher failed: " + safe_error(query.stderr.strip() or query.stdout.strip() or "no diagnostic available"))
    try:
        command = json.loads(query.stdout)
        if not isinstance(command,list) or not command or not all(isinstance(arg,str) for arg in command):
            raise ValueError("Invalid runtime command")
        if Path(command[0]).resolve() != interpreter:
            raise ValueError("Runtime did not select its committed Python")
    except (ValueError,TypeError) as exc:
        raise RuntimeError("Hermes did not return a valid Python runtime.") from exc
    command[0] = str(interpreter)
    return command


def python_command(script, arguments=()):
    interpreter = resolve_runtime()[0]
    boot = "; ".join(["import os,sys,runpy", "sys.path.insert(0," + repr(str(home / "hermes-agent")) + ")", "sys._hermes_pin_default_home=True", "import hermes_bootstrap", "from hermes_cli.env_loader import load_hermes_dotenv", "load_hermes_dotenv()", "sys.path.insert(0," + repr(str(root)) + ")", "sys.argv=" + repr([str(script), *arguments]), "runpy.run_path(" + repr(str(script)) + ",run_name='__main__')"])
    return [interpreter, "-I", "-c", boot]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--script")
    parser.add_argument("--timeout",type=float,default=None)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    arguments = options.arguments[1:] if options.arguments[:1] == ["--"] else options.arguments
    try:
        command = python_command(Path(options.script).resolve(), arguments) if options.script else resolve_runtime(arguments)
        return subprocess.call(command, env=environment, cwd=str(home / "hermes-agent"), timeout=options.timeout)
    except subprocess.TimeoutExpired:
        print("Mika took too long to start. Check the running process and private logs.",file=sys.stderr)
        return 124
    except (OSError,RuntimeError) as exc:
        print("Mika could not start: " + safe_error(str(exc)),file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Adapt the generated launcher so Task Scheduler can observe gateway failure."""
import re


def supervise_vbs(source):
    lines = source.splitlines()
    calls = [i for i,line in enumerate(lines) if line.startswith("sh.Run ")]
    if len(calls) != 1 or "Option Explicit" not in lines:
        raise ValueError("Pinned native launcher format changed")
    index = calls[0]
    match = re.fullmatch(r'sh.Run (.+), 0, False',lines[index])
    if not match or not match[1].endswith('gateway run"'):
        raise ValueError("Unexpected native gateway command")
    command = match[1][:-1] + ' --external-supervisor"'
    lines[index:index+1] = [f"personalExitCode = sh.Run({command}, 0, True)","WScript.Quit personalExitCode"]
    lines.insert(lines.index("Option Explicit")+1,"Dim personalExitCode")
    return "\r\n".join(lines)+"\r\n"

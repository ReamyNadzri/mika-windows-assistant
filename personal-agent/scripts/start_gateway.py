"""Start the local gateway; login automation is optional."""
import os
from hermes_cli import gateway_windows as windows

os.environ["HERMES_GATEWAY_INSTALL_START_ON_LOGIN"] = "0"
name = windows.get_task_name()
existing = windows._live_gateway_pids()
if existing:
    print("Mika is already running. Send a message in Telegram.")
elif windows._query_scheduled_task_xml(name):
    code,out,err = windows._exec_schtasks(["/Run","/TN",name])
    if code:
        raise SystemExit("Scheduled gateway start failed: " + (err or out))
    pids = windows._wait_for_gateway_ready(timeout_s=90,confirm_s=5)
    if len(pids) != 1:
        raise SystemExit("Expected one stable gateway process after scheduled start")
    print("Mika is running through the installed login task.")
else:
    windows.start()
    pids = windows._wait_for_gateway_ready(timeout_s=90, confirm_s=3)
    if len(pids) != 1:
        raise SystemExit("Mika could not connect. Use Manage.ps1 Status for details.")
    print("Mika is running. Send a message in Telegram. Login auto-start is optional.")

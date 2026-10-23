"""Use Hermes' package manager; keep plugin requirements across runtime repairs."""
import os
from pathlib import Path

import pm
from pm.plugin_inputs import Candidates

home = Path(os.environ["HERMES_HOME"])
pm.sync_venv(["telegram", "ddgs"], explicit=True, plugins=Candidates([home / "plugins" / "personal-assistant"]))
print("Managed Telegram, search, chart, and Recycle Bin dependencies prepared. Restart Hermes to use the new generation.")

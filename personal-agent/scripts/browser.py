import argparse
import json
import os

from hermes_personal.browser import ensure_browser

parser = argparse.ArgumentParser()
parser.add_argument("--show", action="store_true", help="Open the dedicated browser window for manual sign-in")
args = parser.parse_args()
print(json.dumps(ensure_browser(os.environ["HERMES_HOME"], visible=args.show)))

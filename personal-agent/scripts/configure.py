"""Validate the owner's private settings and configure a local runtime."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_personal.deployment import configure, default_home, validate_settings

parser = argparse.ArgumentParser()
parser.add_argument("--home", default=str(default_home()))
parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
parser.add_argument("--validate-only", action="store_true", help="Check settings without writing runtime files")
args = parser.parse_args()
try:
    if args.validate_only:
        validate_settings(args.env_file)
        print(json.dumps({"valid": True, "credentials": "values not printed"}))
    else:
        record = configure(args.home, args.env_file, Path(__file__).resolve().parents[2])
        print(json.dumps({"configured": True, "home": record["home"], "upstream_version": record["upstream_version"], "credentials": "stored locally; values not printed"}))
except (ValueError, OSError) as error:
    # Validation errors name settings, never their secret values.
    parser.exit(2, f"Configuration failed: {error}\n")

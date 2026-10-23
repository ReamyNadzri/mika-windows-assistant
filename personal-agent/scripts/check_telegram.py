"""Read bot identity and private-chat user IDs without printing the token."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hermes_personal.deployment import read_env


def private_user_ids(updates):
    found = set()
    for update in updates:
        message = update.get("message", {})
        sender, chat = message.get("from", {}), message.get("chat", {})
        if chat.get("type") == "private" and not sender.get("is_bot", False):
            user_id = sender.get("id")
            if isinstance(user_id, int) and user_id > 0:
                found.add(user_id)
    return sorted(found)


def telegram_get(token, method):
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/" + method)
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise ValueError("Telegram did not accept the request")
    return result["result"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[2] / ".env")
    args = parser.parse_args(argv)
    try:
        token = read_env(args.env_file).get("TELEGRAM_BOT_TOKEN", "")
        if not token or token == "YOUR_BOT_TOKEN":
            raise ValueError("Set your own TELEGRAM_BOT_TOKEN in .env first")
        bot = telegram_get(token, "getMe")
        updates = telegram_get(token, "getUpdates?timeout=0")
        ids = private_user_ids(updates)
        print(json.dumps({"bot_username": bot.get("username"), "private_chat_user_ids": ids,
                          "next": "Put YOUR numeric user ID in TELEGRAM_USER_ID." if ids else "Send /start to YOUR bot in a private chat, then run this check again."}))
        return 0
    except urllib.error.HTTPError as exc:
        # HTTP exception URLs include the bot token: never print them.
        print("Telegram check failed (HTTP " + str(exc.code) + "). Stop any running bot poller before discovering your ID; check the token and network.", file=sys.stderr)
    except (OSError, ValueError, KeyError):
        print("Telegram check failed. Set your own bot token in .env and check your network. No credentials were printed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

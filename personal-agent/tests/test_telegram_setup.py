import urllib.error

from scripts.check_telegram import main, private_user_ids


def test_user_discovery_only_accepts_human_private_chat_senders():
    updates = [{"message": {"chat": {"type": "private"}, "from": {"id": 42}}},
               {"message": {"chat": {"type": "group"}, "from": {"id": 99}}},
               {"message": {"chat": {"type": "private"}, "from": {"id": 100, "is_bot": True}}},
               {"message": {"chat": {"type": "private"}, "from": {"id": 42}}}]
    assert private_user_ids(updates) == [42]


def test_telegram_http_error_never_prints_a_bot_token(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    token = "synthetic-private-token"
    env.write_text("TELEGRAM_BOT_TOKEN=" + token)
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://api.telegram.org/bot"+token+"/getMe", 401, "Unauthorized", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", fail)
    assert main(["--env-file", str(env)]) == 1
    output = capsys.readouterr()
    assert token not in output.err + output.out
    assert "401" in output.err

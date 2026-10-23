import json
from types import SimpleNamespace

import pytest

from hermes_personal.plugin import PersonalAssistant
from hermes_personal.voice import VoiceService, short_spoken_reply, worker_environment
from unittest.mock import AsyncMock


def test_spoken_lead_is_short_and_does_not_read_markdown_or_code():
    reply = "**All set.** Your report is ready.\n\nMore details follow.\n```python\nprint('secret')\n```"
    assert short_spoken_reply(reply) == "All set. Your report is ready."
    assert short_spoken_reply("```python\nprint('do not read code')\n```") == ""
    cleaned = short_spoken_reply("See [the report](https://example.com/private). Saved at D:\\private\\report.pdf")
    assert "http" not in cleaned and "D:" not in cleaned and "the report" in cleaned


def test_spoken_reply_preserves_all_words_and_sentences():
    text = " ".join(["explanation"] * 100) + ". This is the second sentence. This is the final sentence."
    assert short_spoken_reply(text) == text
    assert short_spoken_reply(short_spoken_reply(text)) == text
    assert short_spoken_reply('<tool_call>{"name":"terminal"}</tool_call>') == ""
    assert short_spoken_reply(None) == ""


def test_speech_reads_key_summary_and_ending_question_without_report_details():
    summary = "Your report is ready, sir. I found two items that need your attention."
    question = "Would you like me to open the report?"
    reply = summary + "\n\n## Details\n\nFirst detail. Second detail. Third detail.\n\n" + question
    spoken = short_spoken_reply(reply)
    assert spoken == summary + " " + question
    assert short_spoken_reply(spoken) == spoken


def test_ending_question_is_complete_and_not_repeated():
    assert short_spoken_reply("All set. Shall I open it?\n\nDetails.\n\nShall I open it?") == "All set. Shall I open it?"
    assert short_spoken_reply("The task is blocked.\n\nLong details.\n\nMore context. Which account should I use? Should I wait for you?") == "The task is blocked. Which account should I use? Should I wait for you?"
    assert short_spoken_reply("## Summary\n\nYour files are ready.\n\n## Details\n\nExtra information.") == "Your files are ready."


@pytest.mark.parametrize("details", ["- First detail.\n- Second detail.", "1. First detail.\n2. Second detail."])
def test_list_details_do_not_merge_with_spoken_summary(details):
    assert short_spoken_reply("Your report is ready.\n\n"+details+"\n\nShall I open it?") == "Your report is ready. Shall I open it?"


@pytest.mark.parametrize("question", ["Should I address the letter to Dr. Smith or Dr. Jones?", "Should I use the U.S. account or the U.K. account?", "Should I send it to Mr. A. Smith?"])
def test_closing_question_with_abbreviations_is_preserved(question):
    assert short_spoken_reply("The draft is ready.\n\nDetails stay here.\n\n"+question) == "The draft is ready. "+question


def test_mailbox_keeps_only_latest_reply_and_mute_survives_restart(tmp_path, monkeypatch):
    service = VoiceService(tmp_path, {"enabled": True, "assets_dir": str(tmp_path / "assets")})
    monkeypatch.setattr(service, "ensure_worker", lambda: None)
    assert service.publish("First answer.", key="one")
    assert service.publish("Second answer.", key="two")
    request = json.loads((tmp_path / "voice/request.json").read_text())
    assert request["text"] == "Second answer." and request["action"] == "speak"
    assert not service.publish("Duplicate answer.", key="two")
    service.set_enabled(False)
    assert json.loads((tmp_path / "voice/request.json").read_text())["action"] == "cancel"
    again = VoiceService(tmp_path, {"enabled": True, "assets_dir": str(tmp_path / "assets")})
    assert not again.enabled and not again.publish("Muted.", key="three")


class FakeVoice:
    enabled = False
    def __init__(self):
        self.messages = []
        self.cancelled = 0

    def publish(self, text, *, key):
        self.messages.append((text, key))
        return True

    def cancel(self):
        self.cancelled += 1


def test_overlapping_final_hook_cannot_complete_new_task(tmp_path, monkeypatch):
    import threading
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=FakeVoice())
    assistant.before_turn(session_id="s", turn_id="old", platform="telegram", sender_id="42", user_message="old")
    old = assistant.current_task("s")
    at_lookup, resume, newer_done = threading.Event(), threading.Event(), threading.Event()
    lookup = assistant.current_task
    def paused_lookup(session):
        if threading.current_thread().name == "old-final":
            at_lookup.set()
            assert resume.wait(3)
        return lookup(session)
    monkeypatch.setattr(assistant, "current_task", paused_lookup)
    final = threading.Thread(name="old-final", target=lambda: assistant.after_turn(
        session_id="s", turn_id="old", assistant_response="Old answer."))
    def admit_new():
        assistant.before_turn(session_id="s", turn_id="new", platform="telegram", sender_id="42", user_message="new")
        newer_done.set()
    newer = threading.Thread(target=admit_new)
    final.start()
    assert at_lookup.wait(2)
    newer.start()
    newer_done.wait(0.15)
    resume.set()
    final.join(3)
    newer.join(3)
    assert not final.is_alive() and not newer.is_alive()
    new = assistant.current_task("s")
    assert new != old and assistant.store.get(new)["status"] == "running"
    assert assistant.store.get(old)["summary"] == "Old answer."


def test_only_authorized_final_reply_speaks_and_duplicate_hook_does_not(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    assistant.before_turn(session_id="wrong", platform="telegram", sender_id="99", user_message="hello")
    assistant.after_turn(session_id="wrong", assistant_response="Must not speak.")
    assert not voice.messages
    assistant.before_turn(session_id="owner", platform="telegram", sender_id="42", user_message="hello")
    assistant.after_turn(session_id="owner", assistant_response="Good evening.")
    assistant.after_turn(session_id="owner", assistant_response="Duplicate.")
    assert [text for text, _ in voice.messages] == ["Good evening."]
    assistant.on_stopped()
    assert voice.cancelled == 1


def test_initial_acknowledgement_speaks_once_without_finishing_task(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    assistant.before_turn(session_id="owner", turn_id="t", platform="telegram", sender_id="42", user_message="Check my inbox")
    task = assistant.current_task("owner")
    assistant.on_interim_message(session_id="owner", turn_id="t", surface="telegram", text="I'll check your inbox now, sir.")
    assistant.on_interim_message(session_id="owner", turn_id="t", surface="telegram", text="Another progress update.")
    assert [text for text, _ in voice.messages] == ["I'll check your inbox now, sir."]
    assert assistant.store.get(task)["status"] == "running"
    assistant.after_turn(session_id="owner", turn_id="t", assistant_response="Two emails need your attention.")
    assert len(voice.messages) == 2 and voice.messages[0][1] != voice.messages[1][1]
    assistant.on_interim_message(session_id="owner", turn_id="t", surface="telegram", text="Late acknowledgement.")
    assert len(voice.messages) == 2


@pytest.mark.asyncio
async def test_interim_speech_rejects_unauthorized_superseded_and_stopped_turns(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    assistant.on_interim_message(session_id="stranger", turn_id="t", surface="telegram", text="Ignore this.")
    assistant.before_turn(session_id="s", turn_id="old", platform="telegram", sender_id="42", user_message="Check mail")
    assistant.on_interim_message(session_id="s", turn_id="old", surface="cli", text="Wrong surface.")
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="42", chat_id="42", chat_type="dm"), internal=False, get_command=lambda: None, text="New instruction")
    await assistant.on_inbound(event, SimpleNamespace())
    assistant.on_interim_message(session_id="s", turn_id="old", surface="telegram", text="Stale acknowledgement.")
    assistant.before_turn(session_id="s", turn_id="new", platform="telegram", sender_id="42", user_message="New instruction")
    assistant.on_interim_message(session_id="s", turn_id="old", surface="telegram", text="Old turn.")
    assistant.on_stopped()
    assistant.on_interim_message(session_id="s", turn_id="new", surface="telegram", text="Stopped turn.")
    assert not voice.messages


def test_current_english_preference_reaches_existing_conversations(tmp_path):
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=FakeVoice())
    result = assistant.before_turn(session_id="existing", platform="telegram", sender_id="42", user_message="apa khabar")
    context = result["context"]
    assert "answer in English" in context and "even if the owner writes in another language" in context
    assert "JARVIS manner" in context and "complete sentences" in context
    assert context.index("current reply preference") < context.index("Durable task context")


def test_speech_failure_cannot_break_final_written_reply(tmp_path):
    class BrokenVoice(FakeVoice):
        def publish(self, *args, **kwargs):
            raise OSError("Audio unavailable")
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=BrokenVoice())
    assistant.before_turn(session_id="owner", platform="telegram", sender_id="42", user_message="hello")
    assistant.after_turn(session_id="owner", assistant_response="Your result is ready.")
    assert assistant.store.get(assistant.current_task("owner"))["summary"] == "Your result is ready."


@pytest.mark.asyncio
async def test_new_private_owner_message_cancels_voice_but_strangers_do_not(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    source = SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="99", chat_id="42", chat_type="dm")
    event = SimpleNamespace(source=source, internal=False, get_command=lambda: None, text="hello")
    await assistant.on_inbound(event, SimpleNamespace())
    assert voice.cancelled == 0
    source.user_id = "42"
    await assistant.on_inbound(event, SimpleNamespace())
    assert voice.cancelled == 1


@pytest.mark.asyncio
async def test_superseded_turn_cannot_speak_or_finish_a_newer_turn(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    assistant.before_turn(session_id="s", turn_id="old", platform="telegram", sender_id="42", user_message="old")
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="42", chat_id="42", chat_type="dm"), internal=False, get_command=lambda: None, text="new")
    await assistant.on_inbound(event, SimpleNamespace())
    assistant.after_turn(session_id="s", turn_id="old", assistant_response="Old result.")
    assert not voice.messages
    assistant.before_turn(session_id="s", turn_id="new", platform="telegram", sender_id="42", user_message="new")
    assistant.after_turn(session_id="s", turn_id="old", assistant_response="Late old callback.")
    assert assistant.store.get(assistant.current_task("s"))["status"] == "running"
    assistant.after_turn(session_id="s", turn_id="new", assistant_response="New result.")
    assert [text for text, _ in voice.messages] == ["New result."]


def test_worker_does_not_inherit_gateway_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret")
    monkeypatch.setenv("OTHER_SECRET", "other-secret")
    environment = worker_environment()
    assert not {"provider-secret", "telegram-secret", "other-secret"} & set(environment.values())
    assert environment["HF_HUB_OFFLINE"] == "1"


def test_portable_worker_uses_own_environment_and_public_voice(tmp_path, monkeypatch):
    from hermes_personal import voice
    assets = tmp_path / "assets"
    for name in ("env/Scripts/pythonw.exe", "models/kokoro-v1.0.onnx", "models/voices-v1.0.bin"):
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"asset")
    launches = []
    monkeypatch.setattr(voice.subprocess, "Popen", lambda args, **kwargs: launches.append((args, kwargs)))
    service = VoiceService(tmp_path / "home", {"enabled": True, "assets_dir": str(assets)})
    service.ensure_worker()
    command, options = launches[0]
    assert command[0] == str(assets / "env/Scripts/pythonw.exe")
    assert command[command.index("--voice-id") + 1] == "bm_george"
    assert command[command.index("--assets-dir") + 1] == str(assets)
    assert "--voice-file" not in command
    assert "LocalVoiceStudio" not in " ".join(command)


def test_portable_assets_do_not_require_custom_style(tmp_path):
    from hermes_personal.voice import voice_assets
    for name in ("env/Scripts/python.exe", "models/kokoro-v1.0.onnx", "models/voices-v1.0.bin"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"asset")
    settings = voice_assets({"assets_dir": str(tmp_path)})
    assert settings["voice_id"] == "bm_george" and settings["voice_file"] is None
    with pytest.raises(FileNotFoundError, match="voice"):
        voice_assets({"assets_dir": str(tmp_path), "voice_file": str(tmp_path / "missing.npy")})


@pytest.mark.asyncio
async def test_idle_stop_cancels_audio_without_a_running_agent(tmp_path):
    voice = FakeVoice()
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    source = SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="42", chat_id="42", chat_type="dm")
    await assistant.on_inbound(SimpleNamespace(source=source, internal=False, get_command=lambda: "stop", text="/stop"), SimpleNamespace())
    assert voice.cancelled == 1 and not assistant._tasks


@pytest.mark.asyncio
async def test_private_voice_command_mutes_without_calling_model(tmp_path, monkeypatch):
    voice = VoiceService(tmp_path, {"enabled": True})
    monkeypatch.setattr(voice, "ensure_worker", lambda: None)
    assistant = PersonalAssistant(tmp_path, "42", [tmp_path], voice=voice)
    adapter = SimpleNamespace(send=AsyncMock())
    source = SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="42", chat_id="42", chat_type="dm")
    result = await assistant.on_inbound(SimpleNamespace(source=source, internal=False, get_command=lambda: "voice", text="/voice off"), SimpleNamespace(adapters={"telegram": adapter}))
    assert result["action"] == "skip" and not voice.enabled
    assert not assistant._tasks
    adapter.send.assert_awaited_once()

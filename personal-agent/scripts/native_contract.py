"""Exercise the pinned Telegram authorization and approval APIs without polling."""
import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import ExecApprovalPrompt, SendResult
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource
from hermes_cli.plugins import discover_plugins, get_plugin_manager
from plugins.platforms.telegram.adapter import TelegramAdapter
from tools import approval
from tools.approval_gateway_wait import _ApprovalEntry
from hermes_cli import gateway_windows
from startup_helpers import supervise_vbs


async def main():
    native_launcher = gateway_windows._build_gateway_vbs_script(sys.executable,str(Path(os.environ["HERMES_HOME"])),os.environ["HERMES_HOME"],"")
    supervised = supervise_vbs(native_launcher)
    assert "--external-supervisor" in supervised and "WScript.Quit personalExitCode" in supervised
    discover_plugins()
    manager = get_plugin_manager()
    runtime = next(cb.__self__ for cb in manager._hooks["pre_gateway_dispatch"] if hasattr(getattr(cb, "__self__", None), "owner"))
    owner = runtime.owner
    with tempfile.TemporaryDirectory(prefix="hermes-contract-") as folder:
        assistant = type(runtime)(Path(folder) / "runtime", owner, [Path(folder)])
        # Exercise the actual asynchronous native commentary hook, without
        # contacting Telegram or starting the desktop voice worker.
        assert any(getattr(cb, "__self__", None) is runtime for cb in manager._hooks["on_interim_message"])
        from agent.stream_delivery import StreamDeliveryMixin
        from agent.plugin_stream_hooks import shutdown_plugin_stream_hook_dispatcher
        voice_calls, heard = [], threading.Event()
        def publish(text, *, key):
            voice_calls.append((text, key))
            heard.set()
            return True
        voice_assistant = type(runtime)(Path(folder) / "voice", owner, [Path(folder)], voice=SimpleNamespace(publish=publish))
        voice_assistant.before_turn(session_id="voice-contract", turn_id="ack", platform="telegram", sender_id=owner, user_message="Check inbox")
        agent = SimpleNamespace(session_id="voice-contract", _current_turn_id="ack", _api_call_count=1, model="synthetic", provider="synthetic", platform="telegram")
        agent._stream_hook_base_payload = lambda: StreamDeliveryMixin._stream_hook_base_payload(agent)
        with patch("hermes_cli.plugins.iter_hook_callbacks", side_effect=lambda name: (voice_assistant.on_interim_message,) if name == "on_interim_message" else ()):
            StreamDeliveryMixin._enqueue_stream_hook(agent, "on_interim_message", text="I'll check your inbox now, sir.", already_streamed=False)
            assert heard.wait(3), "Native interim hook did not reach local speech"
            shutdown_plugin_stream_hook_dispatcher()
        assert voice_calls[0][0] == "I'll check your inbox now, sir."
        assert voice_assistant.store.get(voice_assistant.current_task("voice-contract"))["status"] == "running"
        voice_assistant.after_turn(session_id="voice-contract", turn_id="ack", assistant_response="Your inbox summary is ready.")
        assert len(voice_calls) == 2 and voice_calls[0][1] != voice_calls[1][1]
        adapter = TelegramAdapter(PlatformConfig(enabled=True, token="123:synthetic"))
        async def send_prompt(kind, chat, metadata, build, **kwargs):
            text, keyboard, accepted = build()
            accepted(SimpleNamespace(message_id="synthetic"))
            return SendResult(success=True, message_id="synthetic")
        adapter._send_prompt = send_prompt
        adapter.send = AsyncMock(return_value=SendResult(success=True))
        adapter._edit_md_quiet = AsyncMock()
        adapter.resume_typing_for_chat = lambda chat: None
        gateway = SimpleNamespace(adapters={Platform.TELEGRAM:adapter})
        def event(user, chat_type="dm", media=None):
            source = SessionSource(platform=Platform.TELEGRAM, user_id=user, chat_id=owner, chat_type=chat_type)
            return MessageEvent(text="synthetic instruction", source=source, message_id="contract", media_urls=media or [])
        assert (await assistant.on_inbound(event("0", media=[str(Path(folder)/"missing.txt")]), gateway))["action"] == "skip"
        assert (await assistant.on_inbound(event(owner,"group"), gateway))["action"] == "skip"
        assert await assistant.on_inbound(event(owner), gateway) is None
        welcome = event(owner)
        welcome.text = "/start@your_own_bot"
        assert (await assistant.on_inbound(welcome,gateway))["action"] == "skip"
        assert any("/jobs" in str(call.args) and assistant.name in str(call.args) for call in adapter.send.await_args_list)
        command = event(owner)
        command.text = "/approvals off"
        assert (await assistant.on_inbound(command,gateway))["action"] == "skip"
        assistant.before_turn(session_id="contract", platform="telegram", sender_id=owner, user_message="synthetic approved call")
        key = "personal-contract-" + Path(folder).name
        def enqueue(request_id):
            entry = _ApprovalEntry({"request_id":request_id,"description":"exact call " + request_id})
            with approval._lock:
                approval._gateway_queues.setdefault(key, []).append(entry)
            return entry
        async def card(request_id):
            before = set(adapter._approval_state)
            result = await adapter.send_exec_approval(owner, "<synthetic>", key, description="exact call " + request_id)
            assert result.success
            return (set(adapter._approval_state)-before).pop()
        def query(user):
            return SimpleNamespace(from_user=SimpleNamespace(id=user, first_name="Contract"), answer=AsyncMock())
        cb = {"chat_id":owner,"chat_type":"private","thread_id":None,"user_name":"Contract"}
        try:
            a = enqueue("A")
            old = await card("A")
            assert approval.withdraw_gateway_approval(key,"A","synthetic timeout")
            b = enqueue("B")
            fresh = await card("B")
            await adapter._handle_exec_approval_callback(query("0"),f"ea:once:{fresh}",cb)
            assert b.result is None and fresh in adapter._approval_state
            await adapter._handle_exec_approval_callback(query(owner),f"ea:once:{old}",cb)
            assert b.result is None, "Expired button approved a newer request"
            await adapter._handle_exec_approval_callback(query(owner),f"ea:once:{fresh}",cb)
            assert b.result == "once"
            c = enqueue("C")
            await adapter._handle_exec_approval_callback(query(owner),f"ea:once:{fresh}",cb)
            assert c.result is None, "Duplicate tap approved a newer request"
        finally:
            approval.unregister_gateway_notify(key)
    home = Path(os.environ["HERMES_HOME"])
    (home/"logs"/"native-contract.json").write_text(json.dumps({"ok":True,"checks":[{"check":"native_contract","ok":True}]},indent=2),encoding="utf-8")
    print(json.dumps({"native_contract":True,"authorization":"owner private only","approvals":"exact request, expired/duplicate/unauthorized rejected"}))


asyncio.run(main())

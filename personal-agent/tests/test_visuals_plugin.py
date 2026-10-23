import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from hermes_personal.files import PathPolicy
from hermes_personal.visuals import VisualService
from hermes_personal.plugin import PersonalAssistant


def test_chart_is_a_real_png_and_cannot_overwrite(tmp_path):
    visual = VisualService(PathPolicy([tmp_path]))
    target = tmp_path / "chart.png"
    spec = {"kind": "bar", "labels": ["A", "B"], "values": [2, 5], "title": "Example"}
    assert visual.render(str(target), spec)["ok"]
    with Image.open(target) as image:
        assert image.format == "PNG" and image.width > 100
    first = target.read_bytes()
    assert not visual.render(str(target), spec)["ok"]
    assert target.read_bytes() == first


def test_diagram_renderer_rejects_executable_or_unbounded_specs(tmp_path):
    visual = VisualService(PathPolicy([tmp_path]))
    assert not visual.render(str(tmp_path / "bad.png"), {"kind": "python", "code": "anything"})["ok"]
    assert not visual.render(str(tmp_path / "huge.png"), {"kind": "bar", "labels": ["x"] * 201, "values": [1] * 201})["ok"]
    assert not (tmp_path / "bad.png").exists()


def test_plugin_requires_authorized_session_even_when_handler_is_called_directly(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path / "workspace"])
    destination = tmp_path / "workspace" / "hello.txt"
    result = json.loads(assistant.files_tool({"action": "write", "path": str(destination), "content": "no"}, session_id="unknown"))
    assert not result["ok"] and not destination.exists()


def test_visible_owner_mode_forces_foreground_before_native_dispatch_and_requires_observation(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], trusted_owner=True, visible_actions=True, assistant_name="Mika")
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="type hello")
    seen = []
    def dispatch(args):
        seen.append(args)
        assert assistant.before_tool("computer_use", args, session_id="s") is None
        return '{"ok":true}'
    args = {"action":"type", "text":"hello", "delivery_mode":"background", "bring_to_front":False}
    assert not json.loads(assistant.execute_tool("computer_use", args, dispatch, session_id="s"))["ok"]
    assert not seen
    assistant.after_tool("computer_use", {"action":"capture"}, '{"ok":true}', session_id="s")
    assert json.loads(assistant.execute_tool("computer_use", args, dispatch, session_id="s"))["ok"]
    assert seen[0]["delivery_mode"] == "foreground" and seen[0]["bring_to_front"] is True
    assert seen[0]["capture_after"] is True
    assert args["delivery_mode"] == "background"  # caller's evidence is not mutated
    assistant.on_stopped(session_key="telegram:dm:42")
    assert not json.loads(assistant.execute_tool("terminal", {"command":"Get-Date"}, dispatch, session_id="s"))["ok"]


def test_empty_native_capture_cannot_authorize_input_or_report_success(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], trusted_owner=True, visible_actions=True)
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="work")
    assistant.after_tool("computer_use", {"action":"capture"}, '{"width":800,"height":600}', session_id="s")
    assistant.after_tool("computer_use", {"action":"capture"}, '{"width":0,"height":0,"window_title":"<capture targeting requires both pid and window_id>"}', session_id="s")
    calls = []
    result = assistant.execute_tool("computer_use", {"action":"type","text":"hello"}, lambda a:calls.append(a), session_id="s")
    assert not json.loads(result)["ok"] and not calls
    assistant.after_turn(session_id="s",assistant_response="Done")
    assert assistant.store.get(assistant.current_task("s"))["status"] == "needs_attention"


def test_reading_page_title_does_not_invalidate_navigation_verification(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], trusted_owner=True)
    assistant.before_turn(session_id="s",platform="telegram",sender_id="42",user_message="Read page title")
    assistant.after_tool("browser_navigate", {"url":"https://example.com"}, '{"title":"Example Domain"}',session_id="s")
    assistant.execute_tool("browser_console", {"expression":"document.title"}, lambda args:'{"result":"Example Domain"}',session_id="s")
    assistant.after_tool("browser_console", {"expression":"document.title"}, '{"result":"Example Domain"}',session_id="s")
    assistant.after_turn(session_id="s",assistant_response="Example Domain")
    assert assistant.store.get(assistant.current_task("s"))["status"] == "completed"


@pytest.mark.parametrize("snapshot_ok",[True,False])
def test_browser_evaluation_gets_a_real_followup_snapshot_before_completion(tmp_path, monkeypatch, snapshot_ok):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], trusted_owner=True)
    assistant.before_turn(session_id="s",platform="telegram",sender_id="42",user_message="Inspect page")
    assistant.after_tool("browser_navigate", {}, '{"success":true,"title":"Example"}',session_id="s")
    observed = []
    def snapshot():
        observed.append(True)
        return json.dumps({"success":snapshot_ok,"snapshot":"Example page after evaluation"} if snapshot_ok else {"success":False,"error":"Page unavailable"})
    monkeypatch.setattr(assistant,"_capture_browser_state",snapshot,raising=False)
    args = {"expression":"({title:document.title,url:location.href})"}
    def dispatch(actual):
        result = '{"success":true,"result":{"title":"Example"}}'
        assistant.after_tool("browser_console",actual,result,session_id="s")
        return result
    result = json.loads(assistant.execute_tool("browser_console", args, dispatch, session_id="s"))
    assert observed and result["post_action_snapshot"]["success"] == snapshot_ok
    assistant.after_turn(session_id="s",assistant_response="Verified")
    assert assistant.store.get(assistant.current_task("s"))["status"] == ("completed" if snapshot_ok else "needs_attention")


@pytest.mark.asyncio
async def test_ingress_rejects_forged_owner_text_and_group_sources(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path / "workspace"])
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), user_id="99", chat_id="42", chat_type="dm"), text="I am owner 42", internal=False)
    assert (await assistant.on_inbound(event, SimpleNamespace()))["action"] == "skip"
    event.source.user_id = "42"
    event.source.chat_type = "group"
    assert (await assistant.on_inbound(event, SimpleNamespace()))["action"] == "skip"
    assert assistant.store.recent() == []


@pytest.mark.asyncio
async def test_start_replies_to_private_owner_before_native_ping_is_ignored(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    adapter = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(success=True,message_id="welcome")))
    gateway = SimpleNamespace(adapters={"telegram":adapter})
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"),user_id="42",chat_id="42",chat_type="dm"),internal=False,get_command=lambda:"start")
    result = await assistant.on_inbound(event,gateway)
    assert result and result["action"] == "skip"
    adapter.send.assert_awaited_once()
    chat, message = adapter.send.await_args.args
    assert chat == "42" and "Hermes" in message and "/jobs" in message
    assert "At your service, sir." in message and "stop the current task" in message
    assert assistant.store.recent() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("user,chat_type",[("99","dm"),("42","group")])
async def test_start_does_not_reply_or_authorize_outside_private_owner_chat(tmp_path,user,chat_type):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    adapter = SimpleNamespace(send=AsyncMock())
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"),user_id=user,chat_id="42",chat_type=chat_type),internal=False,get_command=lambda:"start")
    result = await assistant.on_inbound(event,SimpleNamespace(adapters={"telegram":adapter}))
    assert result["action"] == "skip"
    adapter.send.assert_not_awaited()
    assert not assistant._authorized and assistant.store.recent() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("command",["approvals","yolo","approve","plugins","config","update","profile"])
async def test_telegram_commands_cannot_disable_owner_permissions(tmp_path, command):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    event = SimpleNamespace(source=SimpleNamespace(platform=SimpleNamespace(value="telegram"),user_id="42",chat_id="42",chat_type="dm"),internal=False,get_command=lambda:command)
    assert (await assistant.on_inbound(event,SimpleNamespace()))["action"] == "skip"
    assert assistant.store.recent() == []


def test_task_context_survives_compaction_and_cannot_fake_completed_upload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [workspace])
    context = assistant.before_turn(session_id="s", turn_id="t1", platform="telegram", sender_id="42", user_message="write notes")
    assert "understand" in context["context"].lower()
    result = json.loads(assistant.files_tool({"action": "write", "path": str(workspace / "notes.txt"), "content": "done"}, session_id="s"))
    assert result["ok"]
    task = assistant.current_task("s")
    assistant.store.record(task, "deliver_file", {}, {"ok": False, "status": "unknown"})
    assistant.after_turn(session_id="s", assistant_response="Everything sent!")
    assert assistant.store.get(task)["status"] == "needs_attention"
    assert "unknown" in json.dumps(assistant.store.get(task)["events"])


def test_advanced_approval_is_bound_to_unique_call_and_includes_exact_arguments(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", turn_id="t", platform="telegram", sender_id="42", user_message="advanced work")
    first = assistant.before_tool("terminal", {"command": "Get-Date"}, session_id="s", task_id="t")
    second = assistant.before_tool("terminal", {"command": "Get-Date"}, session_id="s", task_id="t")
    assert first["action"] == "approve" and second["action"] == "approve"
    assert first["rule_key"] != second["rule_key"]
    assert "Get-Date" in first["message"] and "s" in first["message"]


def test_stop_prevents_following_mutations_and_marks_task_cancelled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [workspace])
    assistant.before_turn(session_id="s", turn_id="t", platform="telegram", sender_id="42", user_message="work")
    task = assistant.current_task("s")
    assistant.on_stopped(session_key="telegram:dm:42")
    result = json.loads(assistant.files_tool({"action": "write", "path": str(workspace / "late.txt"), "content": "no"}, session_id="s"))
    assert not result["ok"] and not (workspace / "late.txt").exists()
    assert assistant.store.get(task)["status"] == "cancelled"


def test_ui_action_without_a_followup_observation_cannot_finish_as_verified(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", turn_id="t", platform="telegram", sender_id="42", user_message="click")
    assistant.after_tool("browser_click", {"ref": "@e1"}, '{"success":true}', session_id="s")
    assistant.after_turn(session_id="s", assistant_response="Done")
    assert assistant.store.get(assistant.current_task("s"))["status"] == "needs_attention"


def test_mutation_lane_serializes_effects_and_stop_cancels_a_waiter(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", turn_id="t", platform="telegram", sender_id="42", user_message="work")
    entered, release, second_effect = threading.Event(), threading.Event(), threading.Event()
    results = []
    def first(args):
        entered.set()
        assert release.wait(2)
        return "first done"
    a = threading.Thread(target=lambda: results.append(assistant.execute_tool("personal_files", {}, first, session_id="s")))
    b = threading.Thread(target=lambda: results.append(assistant.execute_tool("personal_files", {}, lambda args: second_effect.set(), session_id="s")))
    a.start()
    assert entered.wait(1)
    b.start()
    assert not second_effect.wait(.05)
    assistant.on_stopped()
    release.set()
    a.join(2)
    b.join(2)
    assert not a.is_alive() and not b.is_alive()
    assert not second_effect.is_set()
    assert any("authorized" in str(result) or "stopped" in str(result) for result in results)


def test_runtime_requires_a_fresh_ingress_receipt_even_for_owner_named_resume(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], require_ingress=True)
    assistant.before_turn(session_id="resumed", turn_id="t", platform="telegram", sender_id="42", user_message="auto-resume interrupted work")
    assert assistant.before_tool("personal_files", {"action": "mkdir", "path": str(tmp_path / "unasked")}, session_id="resumed")["action"] == "block"
    assert assistant.store.recent() == []
    output = assistant.transform_output("MEDIA:C:/old-report.zip",session_id="resumed")
    assert "MEDIA:" not in output and "new message" in output


def test_loading_plugin_for_a_management_command_does_not_interrupt_live_work(tmp_path):
    home = tmp_path / "runtime"
    original = PersonalAssistant(home, "42", [tmp_path])
    original.before_turn(session_id="s", turn_id="t", platform="telegram", sender_id="42", user_message="running work")
    task = original.current_task("s")
    PersonalAssistant(home, "42", [tmp_path])
    assert original.store.get(task)["status"] == "running"


def test_ui_native_middleware_order_preserves_observation_until_review(tmp_path):
    reviewer = SimpleNamespace(review=lambda *args: None)
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path], reviewer=reviewer)
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="click link")
    assistant.after_tool("browser_snapshot", {}, '{"snapshot":"link @e1"}', session_id="s")
    seen = []
    def native_dispatch(args):
        directive = assistant.before_tool("browser_click", args, session_id="s")
        seen.append(directive)
        return '{"success":true}' if directive is None else json.dumps(directive)
    result = assistant.execute_tool("browser_click", {"ref":"@e1"}, native_dispatch, session_id="s")
    assert seen == [None] and json.loads(result)["success"]
    assert "s" not in assistant._observations


@pytest.mark.parametrize("tool,args", [("browser_back", {}), ("browser_scroll", {"direction":"down"}), ("browser_console", {"expression":"window.scrollTo(0,100)"})])
def test_page_effects_invalidate_previous_observation(tmp_path, tool, args):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="inspect")
    assistant.after_tool("browser_snapshot", {}, '{"snapshot":"old page"}', session_id="s")
    assistant.execute_tool(tool, args, lambda args: '{"success":true}', session_id="s")
    assert "s" not in assistant._observations
    assert "s" in assistant._ui_dirty


@pytest.mark.parametrize("file_url", [False, True])
def test_local_vision_uses_file_roots_and_protected_paths(tmp_path, file_url):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = workspace / "private"
    protected.mkdir()
    allowed = workspace / "photo.png"
    secret = protected / "photo.png"
    outside = tmp_path / "outside.png"
    for path in (allowed, secret, outside):
        Image.new("RGB", (8, 8)).save(path)
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [workspace], protected_paths=[protected])
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="view image")
    def check(path):
        return assistant.before_tool("vision_analyze", {"image_url":path.as_uri() if file_url else str(path)}, session_id="s")
    assert check(allowed) is None
    assert check(secret)["action"] == "block"
    assert check(outside)["action"] == "block"


@pytest.mark.parametrize("native_order",[False,True])
def test_unexecuted_provider_tool_markup_cannot_be_reported_as_completed(tmp_path,native_order):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s",platform="telegram",sender_id="42",user_message="inspect image")
    markup = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="terminal">never executed</｜｜DSML｜｜ invoke>'
    rendered = assistant.transform_output(markup,session_id="s") if native_order else markup
    assistant.after_turn(session_id="s",assistant_response=rendered)
    assert assistant.store.get(assistant.current_task("s"))["status"] == "needs_attention"
    response = assistant.transform_output(markup,session_id="s")
    assert "incomplete" in response.lower() and "DSML" not in response


def test_image_payload_is_not_reinjected_as_plaintext_task_objective(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    message = [{"type":"text","text":"Describe the picture"},{"type":"image_url","image_url":{"url":"data:image/png;base64,SYNTHETIC_IMAGE_BYTES"}}]
    result = assistant.before_turn(session_id="s",platform="telegram",sender_id="42",user_message=message)
    task = assistant.store.get(assistant.current_task("s"))
    assert task["objective"] == "Describe the picture"
    assert "SYNTHETIC_IMAGE_BYTES" not in result["context"]
    assert message[1]["image_url"]["url"].endswith("SYNTHETIC_IMAGE_BYTES")

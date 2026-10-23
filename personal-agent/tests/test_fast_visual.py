import json
import time

import pytest

from hermes_personal.plugin import PersonalAssistant


def assistant_at(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime","42",[tmp_path],trusted_owner=True,visible_actions=True,fast_visual=True)
    assistant.before_turn(session_id="s",platform="telegram",sender_id="42",user_message="Type in the test window")
    assistant.after_tool("computer_use",{"action":"capture"},'{"mode":"ax","width":800,"height":600,"elements":[]}',session_id="s")
    return assistant


def followup(*, multimodal=False, ok=True, width=800):
    action = {"ok":ok,"action":"click","verified":False,"effect":"unverifiable"}
    if multimodal:
        return {"_multimodal":True,"action_result":action,"meta":{"mode":"som","width":width,"height":600},"text_summary":"Fresh window after click","content":[{"type":"text","text":"Fresh window after click"},{"type":"image_url","image_url":{"url":"data:image/png;base64,test-frame"}}]}
    return {"mode":"ax","width":width,"height":600,"elements":[],"summary":"Fresh window after click",**action}


@pytest.mark.parametrize("multimodal,action_meta",[(False,False),(False,True),(True,False)])
def test_post_action_capture_allows_next_input_without_duplicate_capture(tmp_path,multimodal,action_meta):
    assistant = assistant_at(tmp_path)
    def click(args):
        result = followup(multimodal=multimodal)
        if action_meta:
            result["meta"] = {"delivered_chars":3}  # text envelope merges action metadata alongside top-level capture dimensions
        assistant.after_tool("computer_use",args,result,session_id="s")
        return result
    assistant.execute_tool("computer_use",{"action":"click","element":1},click,session_id="s")
    calls = []
    def type_text(args):
        calls.append(args)
        result = followup(multimodal=multimodal)
        assistant.after_tool("computer_use",args,result,session_id="s")
        return result
    assistant.execute_tool("computer_use",{"action":"type","text":"hello"},type_text,session_id="s")
    assert len(calls) == 1 and calls[0]["delivery_mode"] == "foreground"
    assert "s" not in assistant._ui_dirty
    event = assistant.store.get(assistant.current_task("s"))["events"][-1]
    assert "test-frame" not in event["result"]
    if multimodal:
        evidence = json.loads(event["result"])
        assert evidence["action_result"]["effect"] == "unverifiable"  # observation does not invent proof of effect


@pytest.mark.parametrize("bad_result",[followup(ok=False),followup(multimodal=True,ok=False),followup(width=0),{"ok":True}, {"_multimodal":True,"action_result":{"ok":True},"meta":{"width":800,"height":600},"content":[]}])
def test_missing_failed_or_empty_followup_cannot_unlock_input(tmp_path,bad_result):
    assistant = assistant_at(tmp_path)
    def click(args):
        assistant.after_tool("computer_use",args,bad_result,session_id="s")
        return bad_result
    assistant.execute_tool("computer_use",{"action":"click","element":1},click,session_id="s")
    called = []
    result = assistant.execute_tool("computer_use",{"action":"type","text":"hello"},lambda args:called.append(args),session_id="s")
    assert not called and json.loads(result)["ok"] is False


def test_followup_expires_and_stop_still_prevents_input(tmp_path):
    assistant = assistant_at(tmp_path)
    def click(args):
        assistant.after_tool("computer_use",args,followup(),session_id="s")
        return followup()
    assistant.execute_tool("computer_use",{"action":"click"},click,session_id="s")
    assistant._observations["s"]["at"] = time.monotonic()-61
    assert json.loads(assistant.execute_tool("computer_use",{"action":"type"},lambda a:None,session_id="s"))["ok"] is False
    assistant.on_stopped(session_key="telegram:dm:42")
    assert json.loads(assistant.execute_tool("terminal",{},lambda a:None,session_id="s"))["ok"] is False

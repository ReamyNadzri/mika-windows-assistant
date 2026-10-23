from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from hermes_personal.approvals import install_approval_binding
from hermes_personal.plugin import PersonalAssistant


@dataclass
class Prompt:
    session_key: str
    description: str
    actions: list


@pytest.mark.asyncio
async def test_native_cards_cannot_approve_later_requests_or_duplicate_taps(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="work")
    queue = [{"request_id":"A", "description":"call A exact args"}]
    adapter = SimpleNamespace(_approval_state={}, send=AsyncMock(return_value=SimpleNamespace(success=True)), _edit_md_quiet=AsyncMock(), resume_typing_for_chat=lambda chat: None)
    async def native_send(prompt):
        key = next(adapter._approval_counter)
        adapter._approval_state[key] = prompt.session_key
        assert [a[1] for a in prompt.actions] == ["once", "deny"]
        return SimpleNamespace(success=True)
    adapter._send_exec_approval_prompt = native_send
    async def native_public(chat_id, command, session_key, description=None, **kwargs):
        return await adapter._send_exec_approval_prompt(Prompt(session_key, description, actions))
    adapter.send_exec_approval = native_public
    async def authorized(query, cb, denial):
        return query.owner
    adapter._callback_authorized = authorized
    resolved = []
    def resolve(session, choice, *, request_id):
        matches = [item for item in queue if item["request_id"] == request_id]
        resolved.append(request_id)
        for item in matches:
            queue.remove(item)
        return len(matches)
    install_approval_binding(adapter, assistant, pending=lambda s:list(queue), resolve=resolve)
    actions = [("Approve", "once", ""), ("Always", "always", ""), ("Deny", "deny", "")]
    await adapter.send_exec_approval("42", "command", "session", description="call A exact args")
    old = next(iter(adapter._approval_state))
    assert old > 1000
    queue[:] = [{"request_id":"B", "description":"call B exact args"}]
    await adapter.send_exec_approval("42", "command", "session", description="call B exact args")
    new = max(adapter._approval_state)
    query = SimpleNamespace(owner=False, answer=AsyncMock())
    cb = {"chat_id":"42"}
    await adapter._handle_exec_approval_callback(query, f"ea:once:{new}", cb)
    assert not resolved and new in adapter._approval_state
    query.owner = True
    await adapter._handle_exec_approval_callback(query, f"ea:once:{old}", cb)
    assert resolved == ["A"] and queue[0]["request_id"] == "B"
    await adapter._handle_exec_approval_callback(query, f"ea:once:{new}", cb)
    await adapter._handle_exec_approval_callback(query, f"ea:once:{new}", cb)
    assert resolved == ["A", "B"] and queue == []
    assert adapter.send.await_count == 2


@pytest.mark.asyncio
async def test_overlapping_native_renders_keep_their_own_request_binding(tmp_path):
    assistant = PersonalAssistant(tmp_path / "runtime", "42", [tmp_path])
    assistant.before_turn(session_id="s", platform="telegram", sender_id="42", user_message="work")
    queue = [{"request_id":i,"description":i} for i in ("A","B")]
    keys, entered = {}, []
    release = asyncio.Event()
    adapter = SimpleNamespace(_approval_state={}, send=AsyncMock(return_value=SimpleNamespace(success=True)), _edit_md_quiet=AsyncMock(), resume_typing_for_chat=lambda chat:None)
    async def native_send(prompt):
        entered.append(prompt.description)
        key = next(adapter._approval_counter)
        adapter._approval_state[key] = prompt.session_key
        keys[prompt.description] = key
        if prompt.description == "A":
            await release.wait()
        return SimpleNamespace(success=True)
    adapter._send_exec_approval_prompt = native_send
    async def public(chat,command,session,description=None,**kw):
        return await adapter._send_exec_approval_prompt(Prompt(session,description,[("Once","once","")]))
    adapter.send_exec_approval = public
    adapter._callback_authorized = AsyncMock(return_value=True)
    resolved = []
    def resolve(session,choice,*,request_id):
        resolved.append(request_id)
        return 1
    install_approval_binding(adapter,assistant,pending=lambda s:list(queue),resolve=resolve)
    a = asyncio.create_task(adapter.send_exec_approval("42","cmd","s",description="A"))
    await asyncio.sleep(.01)
    b = asyncio.create_task(adapter.send_exec_approval("42","cmd","s",description="B"))
    await asyncio.sleep(.01)
    assert entered == ["A"]
    release.set()
    await asyncio.gather(a,b)
    await adapter._handle_exec_approval_callback(SimpleNamespace(answer=AsyncMock()),f"ea:once:{keys['B']}",{"chat_id":"42"})
    assert resolved == ["B"]

"""Bind native Telegram cards to one approval request, never a session FIFO."""
from dataclasses import replace
import time
import itertools
import secrets
from contextvars import ContextVar
import asyncio


def install_approval_binding(adapter, assistant, *, pending=None, resolve=None):
    if getattr(adapter, "_personal_bound_approvals", False):
        return
    if pending is None or resolve is None:
        from tools.approval import list_gateway_approvals, resolve_gateway_approval
        pending, resolve = list_gateway_approvals, resolve_gateway_approval
    original = adapter._send_exec_approval_prompt
    original_public = adapter.send_exec_approval
    current_binding = ContextVar("personal_approval_card", default=None)
    render_lock = asyncio.Lock()
    cards = {}
    adapter._approval_counter = itertools.count(secrets.randbits(64))

    def public_send(chat_id, command, session_key, description=None, **kwargs):
        requests = [item for item in pending(session_key) if item.get("description") == description]
        request_id = requests[0].get("request_id") if len(requests) == 1 else None
        session = next((s for s, task in assistant._tasks.items() if task and assistant.store.get(task)["status"] == "running"), None)
        task = assistant.current_task(session) if session else None
        binding = (session_key, request_id, task, time.monotonic() + 300)
        async def render():
            token = current_binding.set(binding)
            try:
                return await original_public(chat_id, command, session_key, description=description, **kwargs)
            finally:
                current_binding.reset(token)
        return render()

    async def render_prompt(prompt):
        binding = current_binding.get()
        request_id, task = (binding[1], binding[2]) if binding else (None, None)
        existing = set(adapter._approval_state)
        if not request_id or not task:
            from gateway.platforms.base import SendResult
            return SendResult(success=False, error="No exact live request binding")
        for offset in range(0, len(prompt.description), 3000):
            sent = await adapter.send(assistant.owner, "Action details:\n" + prompt.description[offset:offset + 3000])
            if not sent.success:
                from gateway.platforms.base import SendResult
                return SendResult(success=False, error="Full action details could not be delivered")
        result = await original(replace(prompt, actions=[a for a in prompt.actions if a[1] in {"once", "deny"}]))
        for key in set(adapter._approval_state) - existing:
            cards[key] = binding
        return result

    async def send(prompt):
        # Native prompt allocation and accepted-state callback share one dict.
        # Keep the before/after state claim atomic across concurrent tool gates.
        async with render_lock:
            return await render_prompt(prompt)

    async def callback(query, data, cb):
        if not await adapter._callback_authorized(query, cb, "Private owner chat only"):
            return
        try:
            _, choice, key = data.split(":", 2)
            key = int(key)
        except (ValueError, TypeError):
            await query.answer(text="Invalid approval")
            return
        card = cards.pop(key, None)
        adapter._approval_state.pop(key, None)
        count = 0
        if card and choice in {"once", "deny"}:
            session_key, request_id, task, expiry = card
            active = assistant.store.get(task) if task else None
            if request_id and active and active["status"] == "running" and time.monotonic() <= expiry:
                count = resolve(session_key, choice, request_id=request_id)
        label = ("Approved this call" if choice == "once" else "Denied") if count else "Approval expired or already resolved"
        await query.answer(text=label)
        await adapter._edit_md_quiet(query, label)
        if count and cb.get("chat_id") is not None:
            adapter.resume_typing_for_chat(str(cb["chat_id"]))

    adapter._send_exec_approval_prompt = send
    adapter.send_exec_approval = public_send
    adapter._handle_exec_approval_callback = callback
    adapter._personal_bound_approvals = True

import json

from hermes_personal.state import TaskStore
from hermes_personal.policy import Guard


def test_task_evidence_survives_reopen_and_running_tasks_become_interrupted(tmp_path):
    db = tmp_path / "tasks.db"
    store = TaskStore(db)
    task = store.start("session-a", "Organize reports", update_id="100")
    store.record(task, "personal_files", {"action": "move"}, {"ok": True, "evidence": "destination verified"})
    reopened = TaskStore(db)
    assert reopened.recover_interrupted() == 1
    record = reopened.get(task)
    assert record["status"] == "interrupted"
    assert "destination verified" in json.dumps(record["events"])
    assert reopened.start("session-a", "duplicate", update_id="100") is None


def test_unknown_upload_does_not_become_success_or_automatic_retry(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    task = store.start("s", "send report")
    token = store.begin_delivery(task, "file-hash", "document")
    store.finish_delivery(token, "unknown", error="timeout")
    assert store.delivery(task, "file-hash", "document")["status"] == "unknown"
    assert store.begin_delivery(task, "file-hash", "document") is None


def test_routine_files_allowed_but_generic_execution_requires_approval(tmp_path):
    guard = Guard(owner_id="42", protected_paths=[tmp_path / "credentials"])
    assert guard.check("personal_files", {"action": "mkdir", "path": str(tmp_path / "new")}) is None
    for name in ("terminal", "execute_code", "browser_cdp"):
        assert guard.check(name, {"command": "anything"})["action"] == "approve"


def test_guard_blocks_credentials_and_policy_changes(tmp_path):
    guard = Guard(owner_id="42", protected_paths=[tmp_path / "credentials"])
    assert guard.check("read_file", {"path": str(tmp_path / "credentials" / "key")})["action"] == "block"
    assert guard.check("write_file", {"path": str(tmp_path / "credentials" / "config.yaml"), "content": "off"})["action"] == "block"
    assert guard.check("personal_files", {"action": "delete", "path": str(tmp_path / "file")})["action"] == "approve"


def test_authentication_requires_private_owner_event_and_never_trusts_message_text():
    guard = Guard(owner_id="42", protected_paths=[])
    assert guard.authorized("42", "42", "private")
    assert not guard.authorized("99", "42", "private")
    assert not guard.authorized("42", "-123", "group")
    assert not guard.authorized(None, "42", "private")


def test_uncertain_browser_and_external_submissions_require_approval():
    guard = Guard(owner_id="42", protected_paths=[])
    assert guard.check("browser_navigate", {"url": "https://example.com"}) is None
    assert guard.check("browser_click", {"ref": "@e1"})["action"] == "approve"
    assert guard.check("send_message", {"target": "other", "message": "hello"})["action"] == "block"


def test_browser_file_urls_and_javascript_evaluation_cannot_bypass_file_policy():
    guard = Guard(owner_id="42", protected_paths=[])
    for url in ("file:///C:/Users/example/.env", "javascript:fetch('/send')", "data:text/html,hello"):
        assert guard.check("browser_navigate", {"url": url})["action"] == "block"
    assert guard.check("browser_console", {"expression": "fetch('https://other.example')"})["action"] == "approve"


def test_native_tool_schema_discovery_does_not_need_execution_approval():
    guard = Guard(owner_id="42", protected_paths=[])
    assert guard.check("tool_describe", {"names":["personal_files","terminal"]}) is None
    assert guard.check("tool_search", {"queries":["organize files"]}) is None
    assert guard.check("terminal", {"command":"anything"})["action"] == "approve"


def test_trusted_owner_executes_requested_tools_without_approval_but_keeps_boundaries(tmp_path):
    guard = Guard("42", [tmp_path / "private"], trusted_owner=True)
    for tool, args in [("terminal", {"command":"Get-Date"}), ("execute_code", {"code":"print(1)"}), ("browser_console", {"expression":"document.title"}), ("browser_click", {"ref":"@e1"}), ("computer_use", {"action":"type", "text":"hello"})]:
        assert guard.check(tool, args) is None
    assert not guard.authorized("99", "42", "private")
    assert guard.check("personal_files", {"path":str(tmp_path / "private" / "config.yaml")})["action"] == "block"
    assert guard.check("browser_navigate", {"url":"file:///secret"})["action"] == "block"
    assert guard.check("send_message", {})["action"] == "block"

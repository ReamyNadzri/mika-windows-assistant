"""Tool routing policy; UI interpretation is explicitly best effort."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .files import SENSITIVE_NAMES, SENSITIVE_SUFFIXES, within


class Guard:
    def __init__(self, owner_id, protected_paths, *, trusted_owner=False):
        self.owner_id = str(owner_id)
        self.trusted_owner = trusted_owner
        self.protected = [Path(p).resolve() for p in protected_paths]

    def authorized(self, sender, chat, chat_type):
        return str(sender) == self.owner_id and str(chat) == self.owner_id and chat_type == "private"

    def check(self, tool, args, *, review=None):
        if tool in {"tool_search", "tool_describe"}:
            return None  # Native catalog/schema lookup never executes the named tool.
        if tool == "browser_navigate" and urlsplit(str(args.get("url", ""))).scheme not in {"https", "http"}:
            return {"action": "block", "message": "Browser navigation accepts HTTP(S) pages only; local file and executable URLs are disabled"}
        if tool == "browser_console" and args.get("expression") and not self.trusted_owner:
            return {"action": "approve", "message": "JavaScript evaluation requires approval for this exact call"}
        for key in ("path", "file_path", "image_path", "destination", "target_file"):
            value = args.get(key)
            if isinstance(value, str):
                candidate = Path(value).resolve()
                if any(within(candidate, p) for p in self.protected) or any(p.lower() in SENSITIVE_NAMES or p.lower().startswith(".env") for p in candidate.parts) or candidate.suffix.lower() in SENSITIVE_SUFFIXES:
                    return {"action": "block", "message": "Agent policy, credentials, and browser session files are protected"}
        # The owner explicitly selected automatic execution locally. Keep the
        # ingress/path boundary and disabled background/messaging tools; bypass
        # per-call review only for the tools used to carry out owner requests.
        if self.trusted_owner:
            if tool in {"cronjob", "delegate_task", "skill_manage"}:
                return {"action":"block", "message":"Background schedules and runtime changes are managed locally"}
            if tool in {"terminal", "execute_code"} or tool.startswith("browser_") or tool == "computer_use":
                return None
        if tool in {"send_message", "cronjob", "delegate_task", "skill_manage", "browser_exec", "browser_cdp", "terminal", "execute_code"}:
            if tool == "send_message":
                return {"action": "block", "message": "Use owner-bound deliver_file; external messages need a separately approved workflow"}
            return {"action": "approve", "message": "This exact advanced action requires owner approval"}
        if tool == "personal_files":
            if args.get("action") in {"delete", "permanent_delete"}:
                return {"action": "approve", "message": "Permanent deletion requires owner approval"}
            return None
        if tool in {"deliver_file", "render_visual", "task_update", "task_status", "web_search", "web_extract", "vision_analyze", "session_search", "session_view", "memory", "skills_list", "skill_view", "browser_navigate", "browser_snapshot", "browser_vision", "browser_get_images", "browser_back", "browser_scroll", "browser_console"}:
            return None
        if tool == "computer_use" and args.get("action") in {"capture", "screenshot", "list_apps", "list_windows", "get_state", "status"}:
            return None
        if tool in {"read_file", "search_files", "list_files", "write_file", "patch"}:
            return {"action": "block", "message": "Use personal_files for verified personal filesystem operations"}
        if tool.startswith("browser_") or tool == "computer_use":
            if review:
                return review(tool, args)
            return {"action": "approve", "message": "Inspect the current page/window and approve this interaction"}
        return {"action": "approve", "message": "Unclassified tool action requires owner approval"}

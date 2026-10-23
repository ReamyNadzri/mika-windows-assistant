"""Explicit owner-only uploads with durable uncertainty handling."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .files import digest


class DeliveryService:
    def __init__(self, policy, store, owner_id, transport, *, max_bytes=49_000_000):
        self.policy, self.store = policy, store
        self.owner_id, self.transport = str(owner_id), transport
        self.max_bytes = max_bytes
        self.lock = asyncio.Lock()

    async def send(self, path, mode, task):
        try:
            async with self.lock:
                file = self.policy.resolve(path, exists=True)
                if not file.is_file() or file.stat().st_size == 0:
                    raise ValueError("Upload must be a nonempty regular file")
                if file.stat().st_size > self.max_bytes:
                    raise ValueError("File exceeds the configured Telegram upload limit; split it first")
                if mode not in {"auto", "document", "image"}:
                    raise ValueError("mode must be auto, document, or image")
                is_image = file.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                selected = "image" if mode == "image" or (mode == "auto" and is_image) else "document"
                if selected == "image":
                    from PIL import Image
                    with Image.open(file) as image:
                        image.verify()
                    if file.stat().st_size > 9_000_000:
                        raise ValueError("Image preview exceeds 9 MB; send it as a document")
                fingerprint = digest(file)
                token = self.store.begin_delivery(task, fingerprint, selected)
                if token is None:
                    previous = self.store.delivery(task, fingerprint, selected)
                    return {"ok": previous["status"] == "accepted", "status": previous["status"], "message_id": previous["message_id"], "note": "Existing upload record; no duplicate sent"}
                try:
                    if selected == "image":
                        result = await self.transport.send_image(self.owner_id, str(file))
                    else:
                        result = await self.transport.send_document(self.owner_id, str(file), file_name=file.name)
                    if not isinstance(result, dict):
                        result = vars(result)
                    message_id = result.get("message_id")
                    if not result.get("success", result.get("ok", False)) or not message_id:
                        self.store.finish_delivery(token, "unknown", error="Transport did not return a confirmed message ID")
                        return {"ok": False, "status": "unknown", "error": "Telegram acceptance could not be confirmed; do not automatically resend"}
                    self.store.finish_delivery(token, "accepted", message_id=str(message_id))
                    return {"ok": True, "status": "accepted", "message_id": str(message_id), "filename": file.name, "sha256": fingerprint, "bytes": file.stat().st_size, "evidence": "Telegram accepted this upload; read/open status is unknown"}
                except asyncio.CancelledError:
                    self.store.finish_delivery(token, "unknown", error="Upload interrupted")
                    raise
                except Exception as exc:
                    self.store.finish_delivery(token, "unknown", error=type(exc).__name__)
                    return {"ok": False, "status": "unknown", "error": "Upload outcome uncertain (" + type(exc).__name__ + "); inspect chat before requesting another send"}
        except (OSError, ValueError) as exc:
            return {"ok": False, "status": "rejected", "error": str(exc)}

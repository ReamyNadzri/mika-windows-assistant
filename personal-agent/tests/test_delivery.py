import asyncio

import pytest

from hermes_personal.delivery import DeliveryService
from hermes_personal.files import PathPolicy
from hermes_personal.state import TaskStore


class Transport:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send_document(self, chat_id, file_path, **kwargs):
        self.sent.append((chat_id, file_path, "document"))
        if self.error:
            raise self.error
        return {"success": True, "message_id": "71"}

    async def send_image(self, chat_id, image, **kwargs):
        self.sent.append((chat_id, image, "image"))
        return {"success": True, "message_id": "72"}


@pytest.mark.asyncio
async def test_document_upload_is_bound_to_owner_and_records_acceptance(tmp_path):
    transport = Transport()
    store = TaskStore(tmp_path / "tasks.db")
    task = store.start("s", "send code")
    file = tmp_path / "example.py"
    file.write_text("print('hello')")
    delivery = DeliveryService(PathPolicy([tmp_path]), store, "42", transport)
    result = await delivery.send(str(file), "document", task)
    assert result["ok"] and result["message_id"] == "71"
    assert transport.sent == [("42", str(file.resolve()), "document")]
    repeated = await delivery.send(str(file), "document", task)
    assert repeated["status"] == "accepted" and len(transport.sent) == 1


@pytest.mark.asyncio
async def test_timeout_is_recorded_unknown_and_not_retried(tmp_path):
    transport = Transport(asyncio.TimeoutError())
    store = TaskStore(tmp_path / "tasks.db")
    task = store.start("s", "send")
    file = tmp_path / "report.txt"
    file.write_text("report")
    delivery = DeliveryService(PathPolicy([tmp_path]), store, "42", transport)
    assert (await delivery.send(str(file), "document", task))["status"] == "unknown"
    assert (await delivery.send(str(file), "document", task))["status"] == "unknown"
    assert len(transport.sent) == 1


@pytest.mark.asyncio
async def test_missing_or_oversized_file_never_reaches_transport(tmp_path):
    transport = Transport()
    store = TaskStore(tmp_path / "tasks.db")
    task = store.start("s", "send")
    delivery = DeliveryService(PathPolicy([tmp_path]), store, "42", transport, max_bytes=4)
    assert not (await delivery.send(str(tmp_path / "missing.txt"), "document", task))["ok"]
    large = tmp_path / "large.txt"
    large.write_text("12345")
    assert not (await delivery.send(str(large), "document", task))["ok"]
    assert transport.sent == []

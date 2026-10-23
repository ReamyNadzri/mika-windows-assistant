"""Version-pinned bridge to the already-connected native Telegram adapter.

Uploads intentionally bypass the adapter's fallback-to-text and retry wrapper:
a path message must never be mistaken for acceptance of the actual attachment.
"""


class TelegramTransport:
    def __init__(self, adapter):
        self.adapter = adapter

    async def send_document(self, chat_id, file_path, *, file_name=None):
        bot = self.adapter._bot
        if bot is None:
            raise ConnectionError("Telegram is not connected")
        with open(file_path, "rb") as stream:
            message = await bot.send_document(chat_id=int(chat_id), document=stream, filename=file_name, read_timeout=90, write_timeout=90, connect_timeout=15, pool_timeout=15)
        if not message.document:
            raise RuntimeError("Telegram response did not contain a document")
        return {"success": True, "message_id": str(message.message_id)}

    async def send_image(self, chat_id, image):
        bot = self.adapter._bot
        if bot is None:
            raise ConnectionError("Telegram is not connected")
        with open(image, "rb") as stream:
            message = await bot.send_photo(chat_id=int(chat_id), photo=stream, read_timeout=90, write_timeout=90, connect_timeout=15, pool_timeout=15)
        if not message.photo:
            raise RuntimeError("Telegram response did not contain a photo")
        return {"success": True, "message_id": str(message.message_id)}

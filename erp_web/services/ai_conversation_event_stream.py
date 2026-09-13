"""只通知已提交 history version；消息内容始终从原生历史派生。"""

import asyncio
import json


class ConversationEventStream:
    def __init__(self, *, conversation_id, after_history_version, message_store):
        self.conversation_id = conversation_id
        self.after_history_version = after_history_version
        self.message_store = message_store

    def sse_headers(self):
        return {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "close",
        }

    async def stream(self, write_chunk):
        for _ in range(60):
            version = self.message_store.get_version(self.conversation_id)
            if version > self.after_history_version:
                write_chunk(
                    (
                        "data: "
                        + json.dumps(
                            {"type": "resync_required", "history_version": version}
                        )
                        + "\n\n"
                    ).encode()
                )
                return
            write_chunk(b": keepalive\n\n")
            await asyncio.sleep(1)

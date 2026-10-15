"""图片批次的领域进度；模型消息仍由 Pydantic AI 保存。"""

from typing import Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field


class ImageBatchItemPayload(TypedDict):
    index: int
    source_id: str
    source_url: str
    status: Literal["queued", "running", "success", "failed"]
    result_urls: list[str]
    error: str
    outcome_unknown: bool
    started_at: str
    finished_at: str


class ImageBatchSnapshot(TypedDict):
    conversation_id: str
    product_id: str
    prompt: str
    items: list[ImageBatchItemPayload]
    created_at: str
    updated_at: str
    queued: int
    running: int
    success: int
    failed: int
    total: int
    completed: int
    terminal: bool


class ImageBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    source_id: str
    source_url: str = ""
    status: Literal["queued", "running", "success", "failed"] = "queued"
    result_urls: list[str] = Field(default_factory=list)
    error: str = ""
    outcome_unknown: bool = False
    started_at: str = ""
    finished_at: str = ""


class ImageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    product_id: str
    prompt: str
    items: list[ImageBatchItem]
    created_at: str
    updated_at: str

    def snapshot(self) -> dict:
        counts = {state: sum(row.status == state for row in self.items)
                  for state in ("queued", "running", "success", "failed")}
        return {**self.model_dump(), **counts, "total": len(self.items),
                "completed": counts["success"] + counts["failed"],
                "terminal": not (counts["queued"] or counts["running"])}

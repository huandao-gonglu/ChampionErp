"""图片批次的领域进度；模型消息仍由 Pydantic AI 保存。"""

from typing import Literal, NotRequired, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .image_sets import ImageSetPlan, ImageSetPlanPayload


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
    result_asset_ids: NotRequired[list[str]]
    error_code: NotRequired[str]


class ImageBatchSnapshot(TypedDict):
    conversation_id: str
    product_id: str
    draft_id: str
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
    image_set: NotRequired[ImageSetPlanPayload | None]


class ImageBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    source_id: str
    source_url: str = ""
    status: Literal["queued", "running", "success", "failed"] = "queued"
    result_urls: list[str] = Field(default_factory=list)
    error: str = ""
    error_code: str = ""
    outcome_unknown: bool = False
    started_at: str = ""
    finished_at: str = ""
    result_asset_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def classify_unsent_canvas_timeout(self):
        # 旧桌面执行器将登录和排队超时一律标为结果未知，回执仍保留了尚未发送的明确原因。
        if self.status == "failed" and self.outcome_unknown and self.error in {
            "等待 Google 登录超时，本次尚未发送修图请求；登录后可重新执行。",
            "Gemini 浏览器正在处理另一张图片，本次尚未发送。",
        }:
            self.outcome_unknown = False
        return self


class ImageBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    product_id: str
    draft_id: str = ""
    prompt: str
    items: list[ImageBatchItem]
    created_at: str
    updated_at: str
    image_set: ImageSetPlan | None = None
    request_fingerprint: str = ""
    preparation_fingerprint: str = ""

    def snapshot(self) -> dict:
        counts = {state: sum(row.status == state for row in self.items)
                  for state in ("queued", "running", "success", "failed")}
        return {**self.model_dump(exclude={"request_fingerprint", "preparation_fingerprint"}), **counts, "total": len(self.items),
                "completed": counts["success"] + counts["failed"],
                "terminal": not (counts["queued"] or counts["running"])}

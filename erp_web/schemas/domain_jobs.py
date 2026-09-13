"""持久领域 Job 的查询形状；不包含 Agent 推进状态。"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

JobLifecycleStatus = Literal["running", "success", "failed", "cancelled"]
JobActivityStatus = Literal["pending", "running", "completed", "failed"]


class DomainJobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStateActivity(DomainJobModel):
    """领域 Job 内部子步骤；code/label 已由 Reader 白名单映射。"""

    code: str = Field(min_length=1, max_length=80)
    label: str = Field(default="", max_length=200)
    status: JobActivityStatus
    completed_at: datetime | None = None


class JobStateSnapshot(DomainJobModel):
    """``JobStatusReader`` 的类型化通用快照。

    生命周期决定原生工具何时返回终态；其他字段为有界的对账证据。
    """

    status: JobLifecycleStatus
    error: str = Field(default="", max_length=2000)
    # Job 记录是否真实存在；缺失时生命周期仍按 running 处理（不误判终态），
    # 暂时无法读取不能伪装为成功或失败。
    available: bool = True
    stage_code: str = Field(default="", max_length=120)
    stage_label: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    updated_at: datetime | None = None
    attempt: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0)
    next_check_at: datetime | None = None
    last_external_status: str = Field(default="", max_length=80)
    phase_started_at: datetime | None = None
    activities: tuple[JobStateActivity, ...] = Field(
        default_factory=tuple,
        max_length=50,
    )

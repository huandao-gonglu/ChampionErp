from __future__ import annotations

"""云途物流 Capability：运单预览（direct）与真实创建（task + approval）。

Capability 只使用已保存的可信云途配置，不接受模型侧密钥覆盖。真实创建
的审批摘要与规范化参数由服务端快照函数生成（冻结运单内容与配置指纹），
执行时重算快照复核，模型既不能提供审批 payload，也不能在批准后篡改运单。
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import hashlib
import json

from erp_web.context import AppContext
from erp_web.schemas.ai_tools import TaskApprovalSnapshot
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.schemas.logistics_capabilities import (
    LogisticsShipmentCreateRequest,
    LogisticsShipmentCreateResult,
    LogisticsShipmentPreviewRequest,
    LogisticsShipmentPreviewResult,
)
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.config_service import merge_runtime_secret_section
from erp_web.services.task_approval import verify_execution_approval
from erp_web.runtime_units.yunexpress_client import (
    build_create_package_payload,
    build_create_package_preview,
    normalize_yunexpress_config,
    validate_create_package_payload,
)


class YunExpressClientLike(Protocol):
    def create_package_order(
        self,
        payload: dict[str, Any],
        access_token: str = "",
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json_digest(value: Any) -> str:
    """对任意 JSON 结构做稳定序列化哈希；用于冻结审批参数指纹。"""

    canonical = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _shipment_summary(shipment: Mapping[str, Any]) -> str:
    receiver = (
        shipment.get("receiver")
        if isinstance(shipment.get("receiver"), Mapping)
        else {}
    )
    name = _text(receiver.get("name")) if isinstance(receiver, Mapping) else ""
    packages = (
        shipment.get("packages")
        if isinstance(shipment.get("packages"), (list, tuple))
        else ()
    )
    product_code = _text(shipment.get("product_code"))
    parts = [item for item in (name, f"包裹 {len(packages)} 件", product_code) if item]
    return "；".join(parts) or "云途发货单"


def _resolved_config(context: AppContext) -> dict[str, Any]:
    app_config = context.config.load_app_config()
    stored = app_config.get("yunexpress") if isinstance(app_config, dict) else {}
    return normalize_yunexpress_config(
        merge_runtime_secret_section(
            stored if isinstance(stored, dict) else {},
            {},
        )
    )


@dataclass(frozen=True)
class LogisticsCapabilityScope:
    """物流 Capability 的可信依赖边界。"""

    context: AppContext
    client_factory: Callable[[dict[str, Any]], YunExpressClientLike]


LOGISTICS_SHIPMENT_PREVIEW_TOOL = "logistics_shipment_preview"
LOGISTICS_SHIPMENT_CREATE_TOOL = "logistics_shipment_create"


def _logistics_shipment_approval_snapshot(
    request: LogisticsShipmentCreateRequest,
    scope: LogisticsCapabilityScope,
) -> TaskApprovalSnapshot:
    """服务端生成的发货审批快照：冻结运单内容与已保存配置指纹。"""

    config = _resolved_config(scope.context)
    return TaskApprovalSnapshot(
        summary=f"云途创建发货单：{_shipment_summary(request.shipment)}",
        canonical_payload={
            "config_fingerprint": _canonical_json_digest(config),
            "shipment": dict(request.shipment),
        },
    )


@ai_tool(
    name=LOGISTICS_SHIPMENT_PREVIEW_TOOL,
    description="按已保存的云途配置生成发货请求预览；缺字段时返回错误列表。",
    permission="logistics.read",
    side_effect="none",
    recovery_policy="retry_safe",
    version="1",
)
def logistics_shipment_preview(
    request: LogisticsShipmentPreviewRequest,
    scope: Annotated[LogisticsCapabilityScope, Injected()],
) -> LogisticsShipmentPreviewResult:
    config = _resolved_config(scope.context)
    preview = build_create_package_preview(config, dict(request.shipment))
    errors = (
        preview.get("errors") if isinstance(preview, dict) else []
    ) or []
    if errors:
        raise BusinessCapabilityError(
            "LOGISTICS_PREVIEW_INCOMPLETE",
            "；".join(str(item) for item in errors if str(item).strip())
            or "云途发货请求缺少必要字段。",
        )
    return LogisticsShipmentPreviewResult(
        request_payload=dict(preview) if isinstance(preview, dict) else {},
        message="云途发货请求预览已生成。",
        next_action="确认字段映射后可提交创建发货单任务。",
    )


@ai_tool(
    name=LOGISTICS_SHIPMENT_CREATE_TOOL,
    description=(
        "调用云途创建真实发货单；必须经过审批，审批通过后才会调用外部接口。"
    ),
    permission="logistics.write",
    side_effect="write",
    approval_required=True,
    approval_snapshot=_logistics_shipment_approval_snapshot,
    idempotency="required",
    idempotency_keys=("operation_key",),
    recovery_policy="manual",
    version="1",
)
def logistics_shipment_create(
    request: LogisticsShipmentCreateRequest,
    scope: Annotated[LogisticsCapabilityScope, Injected()],
    execution: Annotated[AiExecutionContext, Injected()],
) -> LogisticsShipmentCreateResult:
    verify_execution_approval(
        execution,
        snapshot=_logistics_shipment_approval_snapshot(request, scope),
        capability_name=LOGISTICS_SHIPMENT_CREATE_TOOL,
        capability_version="1",
        stale_code="LOGISTICS_APPROVAL_STALE",
    )
    config = _resolved_config(scope.context)
    payload = build_create_package_payload(dict(request.shipment), config)
    errors = validate_create_package_payload(payload)
    if errors:
        raise BusinessCapabilityError(
            "LOGISTICS_PAYLOAD_INVALID",
            "；".join(str(item) for item in errors if str(item).strip())
            or "云途下单必填字段缺失。",
        )
    # 下单是外部副作用：HTTP 调用必须受任务剩余时间约束（bounded 20s 上限）；
    # 请求发出后的任何异常（含超时）都不得按普通可重试失败处理，否则自动重试
    # 可能在平台侧重复创建运单。统一上报为结果未知，交由人工核对终态。
    io_timeout = execution.bounded_timeout_seconds(20)
    try:
        result = scope.client_factory(config).create_package_order(
            payload, timeout_seconds=io_timeout
        )
    except BusinessCapabilityError:
        raise
    except TimeoutError as exc:
        raise BusinessCapabilityError(
            "LOGISTICS_CREATE_OUTCOME_UNKNOWN",
            f"物流下单请求超时，平台侧是否已创建运单未知：{exc}",
            retryable=False,
            details={"outcome_unknown": True},
        ) from exc
    except Exception as exc:
        raise BusinessCapabilityError(
            "LOGISTICS_CREATE_OUTCOME_UNKNOWN",
            f"物流下单请求已发出，平台侧结果未知：{exc}",
            retryable=False,
            details={"outcome_unknown": True},
        ) from exc
    response = (
        result.get("response")
        if isinstance(result, dict) and isinstance(result.get("response"), dict)
        else {}
    )
    success = (
        response.get("success") is True
        or response.get("code") in ("", None, "0")
        or bool(response.get("result"))
    )
    if not success:
        raise BusinessCapabilityError(
            "LOGISTICS_CREATE_REJECTED",
            _text(response.get("msg") or response.get("message"))
            or "云途返回创建失败。",
        )
    return LogisticsShipmentCreateResult(
        message="云途发货单已创建。",
        next_action="保存云途订单号、运单号和面单信息到本地订单。",
        response=dict(response),
    )


LOGISTICS_AI_CAPABILITIES = (
    logistics_shipment_preview,
    logistics_shipment_create,
)


__all__ = [
    "LOGISTICS_AI_CAPABILITIES",
    "LOGISTICS_SHIPMENT_CREATE_TOOL",
    "LOGISTICS_SHIPMENT_PREVIEW_TOOL",
    "LogisticsCapabilityScope",
    "YunExpressClientLike",
    "logistics_shipment_create",
    "logistics_shipment_preview",
]

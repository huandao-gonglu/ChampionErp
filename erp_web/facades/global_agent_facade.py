"""全局 Agent HTTP 门面与静态 Capability composition root。"""

from __future__ import annotations

import logging
from typing import Any, Callable

from pydantic import ValidationError

from erp_web.context import AppContext, get_context
from erp_web.facades.category_match_facade import (
    match_category as run_category_match,
)
from erp_web.product_model import PLATFORMS
from erp_web.runtime_units.attribute_fill_capabilities import (
    fill_product_attributes,
)
from erp_web.runtime_units.category_capabilities import match_category
from erp_web.runtime_units.collect_helpers import claim_products_to_platforms
from erp_web.runtime_units.copy_generation import generate_ai_copy_bundle
from erp_web.runtime_units.global_task_tools import (
    build_global_task_planning_toolset,
)
from erp_web.runtime_units.market_prepare_capabilities import (
    prepare_draft_for_market,
)
from erp_web.runtime_units.product_capabilities import (
    prepare_product_images,
    read_product,
    update_product_attributes,
)
from erp_web.runtime_units.publish_capabilities import (
    request_product_publish,
    validate_product_publish,
)
from erp_web.schemas.draft_capabilities import DraftQueryRequest
from erp_web.schemas.global_tasks import (
    AnswerResolutionScope,
    CapabilityError,
    CapabilityResult,
    GlobalTaskIdRequest,
    GlobalTaskInputRequest,
    GlobalTaskResponse,
    GlobalTaskStartRequest,
    LocalGlobalTaskState,
    LocalTaskStep,
    RequiredInput,
)
from erp_web.schemas.market_prepare_capabilities import (
    CategoryMatchRequest,
    DraftPrepareForMarketRequest,
    ProductAttributesFillRequest,
)
from erp_web.schemas.product_capabilities import (
    ProductAttributesUpdateRequest,
    ProductImagesPrepareRequest,
    ProductReadRequest,
)
from erp_web.schemas.publish_capabilities import (
    ProductPublishRequest,
    ProductPublishValidateRequest,
    PublishRequestConfirmation,
)
from erp_web.services.capability_errors import (
    BusinessCapabilityError,
    CapabilityInputRequired,
)
from erp_web.services.draft_query_service import (
    query_drafts,
    resolve_trusted_draft_answer,
)
from erp_web.services.global_agent_service import GlobalAgentService
from erp_web.services.global_task_controller import (
    GlobalTaskCapability,
    GlobalTaskController,
    GlobalTaskControllerError,
    GlobalTaskPlanningOutcome,
    declare_global_task_capability,
)
from erp_web.stores.global_task_store import GlobalTaskStoreError


ResponseWithStatus = tuple[dict[str, Any], int]
logger = logging.getLogger(__name__)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_result(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _completed(summary: str, result: Any) -> CapabilityResult[Any]:
    payload = _json_result(result)
    return CapabilityResult(
        status="completed",
        summary=summary,
        result=payload,
    )


def _run_capability(
    function: Callable[[], CapabilityResult[Any]],
) -> CapabilityResult[Any]:
    try:
        return function()
    except CapabilityInputRequired as exc:
        return CapabilityResult(
            status="needs_input",
            summary=str(exc),
            required_inputs=[
                RequiredInput(
                    key=exc.key,
                    label=exc.label,
                    reason=exc.reason,
                    input_type=exc.input_type,
                    options=list(exc.options),
                    input_owner=exc.input_owner,
                )
            ],
        )
    except BusinessCapabilityError as exc:
        return CapabilityResult(
            status="failed",
            summary=str(exc),
            error=CapabilityError(
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
            ),
        )
    except (ValidationError, ValueError) as exc:
        return CapabilityResult(
            status="failed",
            summary="Capability 输入不符合类型契约。",
            error=CapabilityError(
                code="CAPABILITY_INPUT_INVALID",
                message=str(exc),
            ),
        )


def _require_step_input(
    step: LocalTaskStep,
    key: str,
    *,
    label: str,
    reason: str,
    input_type: str = "text",
) -> Any:
    value = step.inputs.get(key)
    if value in (None, "", [], {}):
        raise CapabilityInputRequired(
            f"{key.upper()}_REQUIRED",
            reason,
            key=key,
            label=label,
            reason=reason,
            input_type=input_type,
        )
    return value


def _step_target_platform(
    task: LocalGlobalTaskState,
    step: LocalTaskStep,
    *,
    required: bool = False,
) -> str:
    """统一解析领域契约的 ``target_platform`` 与计划内部别名。"""

    platform = _text(
        step.inputs.get("target_platform")
        or step.inputs.get("platform")
        or task.platform
    ).lower()
    if required and not platform:
        raise CapabilityInputRequired(
            "TARGET_PLATFORM_REQUIRED",
            "执行当前步骤前必须明确目标平台。",
            key="target_platform",
            label="目标平台",
            reason="请选择当前步骤要处理的目标平台。",
            options=sorted(PLATFORMS),
            input_type="select",
        )
    return platform


def _build_base_capabilities(
    context: AppContext,
) -> dict[str, GlobalTaskCapability]:
    def category_match_for_context(
        request: CategoryMatchRequest,
    ):
        return match_category(
            request,
            product_store=context.products,
            matcher=run_category_match,
        )

    def attributes_fill_for_context(
        request: ProductAttributesFillRequest,
    ):
        return fill_product_attributes(
            request,
            product_store=context.products,
        )

    def drafts_query_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            inputs = step.inputs
            positions = inputs.get("positions")
            request = DraftQueryRequest(
                scope=_text(inputs.get("scope")) or "active",
                target_platform=_text(
                    inputs.get("target_platform") or inputs.get("platform")
                ),
                status=_text(inputs.get("status")),
                keyword=_text(inputs.get("keyword")),
                view=_text(inputs.get("view")) or "summary",
                sort=_text(inputs.get("sort")) or "created_desc",
                limit=int(inputs.get("limit") or 50),
                snapshot_id=_text(inputs.get("snapshot_id")),
                positions=(
                    [int(item) for item in positions]
                    if isinstance(positions, list)
                    else []
                ),
            )
            result = query_drafts(
                request,
                product_store=context.products,
                snapshot_repository=context.global_tasks,
            )
            return _completed(
                f"草稿查询完成，共匹配 {result.total} 个草稿。",
                result,
            )

        return _run_capability(execute)

    def product_read_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            product_id = _text(step.inputs.get("product_id") or task.product_id)
            draft_id = _text(step.inputs.get("draft_id"))
            if not product_id and not draft_id:
                _require_step_input(
                    step,
                    "draft_id",
                    label="商品或草稿",
                    reason="请先选择要读取的商品或草稿。",
                )
            result = read_product(
                ProductReadRequest(
                    product_id=product_id,
                    draft_id=draft_id,
                    platform=_step_target_platform(task, step),
                    site=_text(step.inputs.get("site")),
                ),
                product_store=context.products,
            )
            return _completed("商品事实读取完成。", result)

        return _run_capability(execute)

    def category_match_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要匹配类目的草稿。",
                )
            )
            result = category_match_for_context(
                CategoryMatchRequest(
                    draft_id=draft_id,
                    target_platform=_step_target_platform(
                        task,
                        step,
                        required=True,
                    ),
                    site=_text(step.inputs.get("site")),
                    category_id=_text(step.inputs.get("category_id")),
                ),
            )
            return _completed("目标市场类目已匹配并保存。", result)

        return _run_capability(execute)

    def attributes_fill_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要填写属性的草稿。",
                )
            )
            provided = step.inputs.get("provided_attributes")
            values = dict(provided) if isinstance(provided, dict) else {}
            result = attributes_fill_for_context(
                ProductAttributesFillRequest(
                    draft_id=draft_id,
                    target_platform=_step_target_platform(
                        task,
                        step,
                        required=True,
                    ),
                    site=_text(step.inputs.get("site")),
                    provided_attributes=values,
                ),
            )
            return _completed("平台必填属性已填写并保存。", result)

        return _run_capability(execute)

    def draft_prepare_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要准备到目标市场的草稿。",
                )
            )
            provided = step.inputs.get("provided_attributes")
            provided_attributes = (
                dict(provided) if isinstance(provided, dict) else {}
            )
            raw_pricing = step.inputs.get("pricing_input")
            pricing_input = (
                dict(raw_pricing) if isinstance(raw_pricing, dict) else {}
            )
            raw_assets = step.inputs.get("asset_ids")
            asset_ids = (
                [_text(value) for value in raw_assets if _text(value)]
                if isinstance(raw_assets, list)
                else ([_text(raw_assets)] if _text(raw_assets) else [])
            )
            result = prepare_draft_for_market(
                DraftPrepareForMarketRequest(
                    draft_id=draft_id,
                    target_platform=_step_target_platform(
                        task,
                        step,
                        required=True,
                    ),
                    site=_text(step.inputs.get("site")),
                    category_id=_text(step.inputs.get("category_id")),
                    provided_attributes=provided_attributes,
                    asset_ids=asset_ids,
                    pricing_input=pricing_input,
                    regenerate_copy=bool(
                        step.inputs.get("regenerate_copy", False)
                    ),
                ),
                product_store=context.products,
                claim_target_drafts=(
                    lambda product_ids, platforms: claim_products_to_platforms(
                        product_ids,
                        platforms,
                        context=context,
                    )
                ),
                copy_generator=(
                    lambda product,
                    source_platform,
                    target_market,
                    language,
                    mode,
                    app_config: generate_ai_copy_bundle(
                        product,
                        source_platform,
                        target_market,
                        language,
                        mode,
                        app_config,
                        app_dir=context.paths.app_dir,
                    )
                ),
                app_config_loader=context.config.load_app_config,
                category_capability=lambda request, **kwargs: (
                    category_match_for_context(request)
                ),
                attribute_capability=lambda request, **kwargs: (
                    attributes_fill_for_context(request)
                ),
                copy_operation_key=(
                    f"global-task:{task.task_id}:step:{step.step_id}:copy"
                ),
            )
            return _completed("目标市场草稿已准备完成。", result)

        return _run_capability(execute)

    def attributes_update_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要修改的草稿。",
                )
            )
            updates = _require_step_input(
                step,
                "updates",
                label="属性值",
                reason="请提供需要设置的属性和值。",
                input_type="json_object",
            )
            if not isinstance(updates, dict):
                raise BusinessCapabilityError(
                    "PRODUCT_ATTRIBUTE_UPDATES_INVALID",
                    "属性更新必须是字段到目标值的对象。",
                )
            result = update_product_attributes(
                ProductAttributesUpdateRequest(
                    draft_id=draft_id,
                    platform=_step_target_platform(task, step),
                    site=_text(step.inputs.get("site")),
                    updates=updates,
                ),
                product_store=context.products,
            )
            return _completed("草稿属性已按目标值保存。", result)

        return _run_capability(execute)

    def images_prepare_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要准备图片的草稿。",
                )
            )
            asset_ids = step.inputs.get("asset_ids")
            result = prepare_product_images(
                ProductImagesPrepareRequest(
                    draft_id=draft_id,
                    asset_ids=(
                        [_text(item) for item in asset_ids if _text(item)]
                        if isinstance(asset_ids, list)
                        else []
                    ),
                ),
                product_store=context.products,
            )
            return _completed("草稿图片已准备完成。", result)

        return _run_capability(execute)

    def publish_validate_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要发布的草稿。",
                )
            )
            result = validate_product_publish(
                ProductPublishValidateRequest(
                    draft_id=draft_id,
                    platform=_step_target_platform(task, step),
                    site=_text(step.inputs.get("site")),
                ),
                context=context,
            )
            summary = (
                "发布校验已通过。"
                if result.passed
                else f"发布校验未通过，共有 {len(result.errors)} 个错误。"
            )
            return _completed(summary, result)

        return _run_capability(execute)

    def publish_request_capability(
        task: LocalGlobalTaskState,
        step: LocalTaskStep,
    ) -> CapabilityResult[Any]:
        def execute() -> CapabilityResult[Any]:
            if (
                task.publish_confirmation.status != "confirmed"
                or task.publish_confirmation.confirmed_at is None
                or not step.operation_key
                or task.publish_idempotency_key != step.operation_key
            ):
                raise BusinessCapabilityError(
                    "PUBLISH_CONFIRMATION_REQUIRED",
                    "发布请求缺少与当前校验摘要绑定的人工确认。",
                )
            draft_id = _text(
                _require_step_input(
                    step,
                    "draft_id",
                    label="草稿",
                    reason="请先选择要发布的草稿。",
                )
            )
            result = request_product_publish(
                ProductPublishRequest(
                    draft_id=draft_id,
                    platform=_step_target_platform(task, step),
                    site=_text(step.inputs.get("site")),
                    idempotency_key=step.operation_key,
                    confirmation=PublishRequestConfirmation(
                        task_id=task.task_id,
                        step_id=step.step_id,
                        validation_digest=(
                            task.publish_confirmation.validation_digest
                        ),
                        confirmed_at=task.publish_confirmation.confirmed_at,
                    ),
                ),
                publishing_bus=context.publishing_bus,
                context=context,
            )
            return CapabilityResult(
                status="in_progress",
                summary="发布任务已提交，正在等待平台真实终态。",
                job_id=result.job_id,
            )

        return _run_capability(execute)

    # 只读/确定性校验可安全重放；写能力必须由真实 owner 消费稳定
    # operation_key。当前 publish bus 已具备该契约；其余写能力若在 running
    # 中断则明确停在人工核对，不做未知副作用重放。
    return {
        "drafts.query": declare_global_task_capability(
            drafts_query_capability,
            recovery_policy="retry_safe",
        ),
        "draft.prepare_for_market": declare_global_task_capability(
            draft_prepare_capability,
            recovery_policy="manual",
        ),
        "product.read": declare_global_task_capability(
            product_read_capability,
            recovery_policy="retry_safe",
        ),
        "category.match": declare_global_task_capability(
            category_match_capability,
            recovery_policy="manual",
        ),
        "product.attributes.fill": declare_global_task_capability(
            attributes_fill_capability,
            recovery_policy="manual",
        ),
        "product.attributes.update": declare_global_task_capability(
            attributes_update_capability,
            recovery_policy="manual",
        ),
        "product.images.prepare": declare_global_task_capability(
            images_prepare_capability,
            recovery_policy="manual",
        ),
        "product.publish.validate": declare_global_task_capability(
            publish_validate_capability,
            recovery_policy="retry_safe",
        ),
        "product.publish.request": declare_global_task_capability(
            publish_request_capability,
            recovery_policy="idempotent",
        ),
    }


GLOBAL_TASK_CAPABILITY_NAMES = frozenset(
    {
        "drafts.query",
        "draft.prepare_for_market",
        "product.read",
        "category.match",
        "product.attributes.fill",
        "product.attributes.update",
        "product.images.prepare",
        "product.publish.validate",
        "product.publish.request",
    }
)


def build_global_task_capabilities(
    context: AppContext,
) -> dict[str, GlobalTaskCapability]:
    capabilities = _build_base_capabilities(context)
    if frozenset(capabilities) != GLOBAL_TASK_CAPABILITY_NAMES:
        raise RuntimeError("全局任务静态 Capability map 与声明清单不一致。")
    return capabilities


def build_global_task_controller(
    context: AppContext | None = None,
) -> GlobalTaskController:
    context = context or get_context()
    capabilities = build_global_task_capabilities(context)

    def planner(
        task: LocalGlobalTaskState,
        supplement: str,
    ) -> GlobalTaskPlanningOutcome:
        # state/cancel 等只读边界也会装配 Controller；只有实际规划时才加载
        # 模型配置和 AgentFactory，避免普通轮询产生无关依赖与配置失败面。
        agent_service = GlobalAgentService(
            app_dir=context.paths.app_dir,
            app_config=context.config.load_app_config(),
            message_store=context.pydantic_messages,
            snapshot_reader=context.global_tasks,
            product_store=context.products,
            allowed_capabilities=frozenset(capabilities),
        )
        toolset = build_global_task_planning_toolset(
            products=context.products,
            global_tasks=context.global_tasks,
            recent_snapshot_id=task.draft_query_snapshot_id,
        )
        goal = task.goal
        if supplement.strip():
            goal += "\n用户补充说明：" + supplement.strip()
        run = agent_service.plan(
            goal=goal,
            toolset=toolset,
            product_id=task.product_id,
            platform=task.platform,
            recent_snapshot_id=task.draft_query_snapshot_id,
        )
        return GlobalTaskPlanningOutcome(
            decision=run.decision,
            finish=run.finish,
            trusted_answer=getattr(run, "trusted_answer", None),
        )

    return GlobalTaskController(
        store=context.global_tasks,
        planner=planner,
        capabilities=capabilities,
        publish_status_reader=(
            lambda job_id: context.publishing_bus.get_public_status(job_id)
        ),
        answer_resolver=(
            lambda task, decision: resolve_trusted_draft_answer(
                decision,
                product_store=context.products,
                snapshot_repository=context.global_tasks,
                resolution_scope=AnswerResolutionScope(
                    expected_product_id=task.product_id,
                    expected_target_platform=task.platform,
                ),
            )
        ),
    )


def _success(task: LocalGlobalTaskState) -> ResponseWithStatus:
    response = GlobalTaskResponse(
        task=task,
        task_id=task.task_id,
    )
    return response.model_dump(mode="json"), 200


def _error(exc: Exception) -> ResponseWithStatus:
    if isinstance(exc, ValidationError):
        return {
            "ok": False,
            "error": "请求字段不符合全局任务契约。",
            "error_code": "GLOBAL_TASK_REQUEST_INVALID",
        }, 400
    if isinstance(exc, GlobalTaskControllerError):
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, exc.status_code
    if isinstance(exc, GlobalTaskStoreError):
        status = 404 if exc.code == "GLOBAL_TASK_NOT_FOUND" else 409
        return {
            "ok": False,
            "error": str(exc),
            "error_code": exc.code,
        }, status
    logger.exception("全局任务 HTTP 门面发生未处理异常", exc_info=exc)
    return {
        "ok": False,
        "error": "全局任务处理失败，请稍后重试。",
        "error_code": "GLOBAL_TASK_REQUEST_FAILED",
    }, 500


def start_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskStartRequest.model_validate(body)
        context = get_context()
        task = build_global_task_controller(context).create_task(request)
        return _success(task)
    except Exception as exc:
        return _error(exc)


def get_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _success(build_global_task_controller().get_state(request.task_id))
    except Exception as exc:
        return _error(exc)


def submit_global_task_input_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskInputRequest.model_validate(body)
        return _success(build_global_task_controller().submit_input(request))
    except Exception as exc:
        return _error(exc)


def confirm_global_task_publish_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _success(
            build_global_task_controller().confirm_publish(request.task_id)
        )
    except Exception as exc:
        return _error(exc)


def cancel_global_task_payload(body: dict[str, Any]) -> ResponseWithStatus:
    try:
        request = GlobalTaskIdRequest.model_validate(body)
        return _success(build_global_task_controller().cancel(request.task_id))
    except Exception as exc:
        return _error(exc)


__all__ = [
    "GLOBAL_TASK_CAPABILITY_NAMES",
    "ResponseWithStatus",
    "build_global_task_capabilities",
    "build_global_task_controller",
    "cancel_global_task_payload",
    "confirm_global_task_publish_payload",
    "get_global_task_payload",
    "start_global_task_payload",
    "submit_global_task_input_payload",
]

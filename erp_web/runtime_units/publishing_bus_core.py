# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import hmac
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Protocol

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.publish import (
    PublishJobPlatformSummary,
    PublishJobSiteToSellSummary,
    PublishJobSummary,
)

from .publish_confirmation import (
    canonical_publish_digest,
    resolve_publish_store_binding,
)
from .publish_context import PreparedPublishContext, prepare_publish_context


class PublishingAdapter(Protocol):
    def resolve_category(self, product: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        ...

    def required_attributes_missing(
        self,
        context: PreparedPublishContext,
        config: dict[str, Any],
    ) -> list[str]:
        ...

    def publish(self, product: dict[str, Any], platform: str, config: dict[str, Any]) -> dict[str, Any]:
        ...

    def validate_payload(self, payload: Any, config: dict[str, Any]) -> list[str]:
        ...

    def publish_payload(
        self,
        payload: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        ...


class PublishingJobStore(Protocol):
    """Persistence contract implemented by ``erp_web.db.ErpDatabase``."""

    def create_publish_job(
        self,
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """原子保存新幂等映射；键已存在时返回绑定的任务。"""
        ...

    def save_publish_job(self, state: dict[str, Any]) -> None:
        ...

    def load_publish_job(self, job_id: str) -> dict[str, Any]:
        ...

    def load_publish_job_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ...

    def list_pending_publish_jobs(self) -> list[dict[str, Any]]:
        ...

    def list_publish_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> tuple[list[dict[str, Any]], str]:
        ...


class PublishIdempotencyConflictError(RuntimeError):
    """可信幂等键被用于不同的发布事实。"""

    code = "PUBLISH_IDEMPOTENCY_CONFLICT"

    def __init__(self) -> None:
        super().__init__("发布幂等键已绑定到不同的商品、平台、目标或发布确认。")


class PublishApprovalBindingError(RuntimeError):
    """持久化的人工确认与 worker 即将外发的事实不再一致。"""

    code = "PUBLISH_APPROVAL_BINDING_INVALID"


def _publish_exception_retryable(exc: BaseException) -> bool:
    """按类型化错误契约决定总线是否重试。

    - 确认绑定失效是确定性失败，重试只会再次被阻止；
    - 平台 HTTP 边界分类出的 :class:`PublishAdapterError` 以自身
      ``retryable`` 为准（限流/锁/5xx/超时等瞬时失败才重试）；
    - 未被分类的异常默认不可重试，避免确定性 4xx 或本地错误
      被总线反复外发。
    """

    if isinstance(exc, PublishApprovalBindingError):
        return False
    if isinstance(exc, PublishAdapterError):
        return bool(exc.retryable)
    return False


ACTIVE_JOB_STATUSES = frozenset({"pending", "queued", "running", "retrying"})
SUCCESS_JOB_STATUSES = frozenset(
    {"success", "finished", "published", "real_publish_success"}
)
FAILED_JOB_STATUSES = frozenset(
    {"failed", "error", "blocked", "real_publish_failed"}
)
OUTCOME_UNKNOWN_JOB_STATUS = "outcome_unknown"


def _publish_job_display_status(state: dict[str, Any]) -> str:
    raw_platforms = (
        state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
    )
    platform_states = [
        item
        for item in raw_platforms.values()
        if isinstance(item, dict)
    ]
    statuses = {
        str(item.get("status") or item.get("stage") or "").strip().lower()
        for item in platform_states
    }
    if statuses & ACTIVE_JOB_STATUSES:
        if statuses <= {"pending", "queued"}:
            return "queued"
        return "running"
    if OUTCOME_UNKNOWN_JOB_STATUS in statuses:
        return OUTCOME_UNKNOWN_JOB_STATUS

    has_success = bool(statuses & SUCCESS_JOB_STATUSES)
    has_failure = bool(statuses & FAILED_JOB_STATUSES)
    if has_success and has_failure:
        return "partial"
    if has_failure:
        return "failed"
    if has_success and statuses <= SUCCESS_JOB_STATUSES:
        return "success"

    root_status = str(state.get("status") or "").strip().lower()
    if root_status in ACTIVE_JOB_STATUSES:
        return "queued" if root_status in {"pending", "queued"} else "running"
    if root_status == OUTCOME_UNKNOWN_JOB_STATUS:
        return OUTCOME_UNKNOWN_JOB_STATUS
    if root_status in FAILED_JOB_STATUSES:
        return "failed"
    if root_status in SUCCESS_JOB_STATUSES:
        return "success"
    return root_status or "queued"


def _publish_job_sites_to_sell(
    state: dict[str, Any],
    platform: str,
    parent_site: str,
) -> list[PublishJobSiteToSellSummary]:
    """投影任务冻结的销售目标，禁止把完整发布内容带入列表。"""

    approvals = (
        state.get("approved_publications")
        if isinstance(state.get("approved_publications"), dict)
        else {}
    )
    approval = approvals.get(platform)
    payload = (
        approval.get("payload")
        if isinstance(approval, dict)
        and isinstance(approval.get("payload"), dict)
        else {}
    )
    raw_targets = payload.get("sites_to_sell")
    if not isinstance(raw_targets, list):
        product = (
            state.get("product")
            if isinstance(state.get("product"), dict)
            else {}
        )
        drafts = (
            product.get("drafts")
            if isinstance(product.get("drafts"), dict)
            else {}
        )
        draft = next(
            (
                value
                for key, value in drafts.items()
                if str(key or "").strip().lower() == platform.strip().lower()
                and isinstance(value, dict)
            ),
            {},
        )
        raw_targets = draft.get("sites_to_sell")
        if not isinstance(raw_targets, list):
            target_sites = (
                draft.get("target_sites")
                if isinstance(draft.get("target_sites"), list)
                else []
            )
            parent_key = parent_site.strip().lower()
            matching_target = next(
                (
                    target
                    for target in target_sites
                    if isinstance(target, dict)
                    and str(target.get("platform") or platform).strip().lower()
                    == platform.strip().lower()
                    and (
                        not parent_key
                        or str(target.get("site") or "").strip().lower()
                        == parent_key
                    )
                    and isinstance(target.get("sites_to_sell"), list)
                ),
                {},
            )
            raw_targets = matching_target.get("sites_to_sell")
    if not isinstance(raw_targets, list):
        return []

    targets: list[PublishJobSiteToSellSummary] = []
    seen_sites: set[str] = set()
    parent_site_id = parent_site.strip().upper()
    for raw in raw_targets:
        if not isinstance(raw, dict):
            continue
        site_id = str(raw.get("site_id") or "").strip().upper()
        logistic_type = str(raw.get("logistic_type") or "").strip().lower()
        if (
            not site_id
            or not logistic_type
            or site_id == parent_site_id
            or site_id in seen_sites
        ):
            continue
        seen_sites.add(site_id)
        targets.append(
            {
                "site_id": site_id,
                "logistic_type": logistic_type,
            }
        )
    return sorted(
        targets,
        key=lambda target: (target["site_id"], target["logistic_type"]),
    )


def _publish_job_summary(state: dict[str, Any]) -> PublishJobSummary:
    product = state.get("product") if isinstance(state.get("product"), dict) else {}
    raw_platforms = (
        state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
    )
    platforms: list[PublishJobPlatformSummary] = []
    for platform, raw in sorted(raw_platforms.items()):
        item = raw if isinstance(raw, dict) else {}
        parent_site = str(item.get("site") or "")
        platforms.append(
            {
                "platform": str(platform),
                "draft_id": str(item.get("draft_id") or ""),
                "site": parent_site,
                "sites_to_sell": _publish_job_sites_to_sell(
                    state,
                    str(platform),
                    parent_site,
                ),
                "status": str(item.get("status") or ""),
                "stage": str(item.get("stage") or ""),
                "attempts": int(item.get("attempts") or 0),
                "error": str(item.get("error") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )

    error = next((item["error"] for item in platforms if item["error"]), "")
    attempts = max((item["attempts"] for item in platforms), default=0)
    stage = next(
        (item["stage"] for item in reversed(platforms) if item["stage"]),
        "",
    )
    draft_id = str(
        state.get("draft_id")
        or product.get("current_draft_id")
        or product.get("draft_id")
        or ""
    )
    if not draft_id:
        draft_id = next(
            (item["draft_id"] for item in platforms if item["draft_id"]),
            "",
        )
    drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
    if not draft_id:
        for platform in platforms:
            draft = drafts.get(platform["platform"])
            if isinstance(draft, dict) and draft.get("draft_id"):
                draft_id = str(draft["draft_id"])
                break
    return {
        "job_id": str(state.get("job_id") or ""),
        "product_id": str(product.get("product_id") or state.get("product_id") or ""),
        "product_name": str(state.get("product_name") or product.get("name") or ""),
        "draft_id": draft_id,
        "status": _publish_job_display_status(state),
        "raw_status": str(state.get("status") or ""),
        "stage": stage,
        "attempts": attempts,
        "error": error,
        "platforms": platforms,
        "created_at": str(state.get("created_at") or ""),
        "updated_at": str(state.get("updated_at") or ""),
    }


class PublishingBus:
    """Publish queue with SQLite-backed job state.

    Job state never contains store credentials/config: the platform config is
    fetched from ``config_provider`` (store_auth-backed) at execution time.
    """

    def __init__(
        self,
        store: PublishingJobStore,
        adapters: dict[str, PublishingAdapter],
        config_provider: Callable[[], dict[str, Any]] | None = None,
        terminal_callback: (
            Callable[[dict[str, Any]], dict[str, Any] | None] | None
        ) = None,
        max_workers: int = 6,
        max_retries: int = 1,
        retry_delay_seconds: float = 0.25,
        auto_resume_pending: bool = True,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.config_provider = config_provider or (lambda: {})
        self.terminal_callback = terminal_callback
        self.max_retries = max(0, int(max_retries))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="PublishingBus")
        self._lock = threading.RLock()
        self._futures: dict[str, list[Future[Any]]] = {}
        self._reconciling: set[tuple[str, str]] = set()
        if auto_resume_pending:
            self.recover_pending_jobs()

    def enqueue(
        self,
        product: dict[str, Any],
        platforms: list[str],
        *,
        targets: dict[str, dict[str, Any]],
        idempotency_key: str,
        approved_publications: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        trusted_key = str(idempotency_key or "").strip()
        if not trusted_key:
            raise ValueError("发布任务缺少可信 idempotency_key。")

        selected: list[str] = []
        for raw_platform in platforms:
            platform = str(raw_platform or "").strip().lower()
            if platform in self.adapters and platform not in selected:
                selected.append(platform)
        if not selected:
            raise ValueError("请选择至少一个可发布平台。")

        product_id = str(product.get("product_id") or "").strip()
        if not product_id:
            raise ValueError("发布任务缺少 product_id。")
        bindings: dict[str, dict[str, str]] = {}
        for platform in selected:
            raw = targets.get(platform) if isinstance(targets.get(platform), dict) else {}
            draft_id = str(raw.get("draft_id") or "").strip()
            site = str(raw.get("site") or "").strip()
            target_product_id = str(raw.get("product_id") or product_id).strip()
            if not draft_id or not site:
                raise ValueError(f"{platform} 发布任务缺少 draft_id 或 site。")
            if target_product_id != product_id:
                raise ValueError(
                    f"{platform} 草稿绑定商品 {target_product_id} 与发布商品 {product_id} 不一致。"
                )
            bindings[platform] = {
                "draft_id": draft_id,
                "site": site,
                "product_id": product_id,
            }

        approvals = self._normalize_approved_publications(
            approved_publications,
            selected,
        )
        self._validate_approved_publications(
            product_id=product_id,
            bindings=bindings,
            approved_publications=approvals,
        )
        idempotency_facts = self._build_idempotency_facts(
            product_id=product_id,
            platforms=selected,
            bindings=bindings,
            approved_publications=approvals,
        )

        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        now = current_time()
        state = {
            "job_id": job_id,
            "idempotency_key": trusted_key,
            "idempotency_facts": idempotency_facts,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "draft_id": next(iter(bindings.values()))["draft_id"] if len(bindings) == 1 else "",
            "product_name": str(product.get("name") or ""),
            "product": copy.deepcopy(product),
            **(
                {"approved_publications": approvals}
                if approvals
                else {}
            ),
            "platforms": {
                platform: self._new_platform_state(
                    platform,
                    now,
                    bindings[platform],
                )
                for platform in selected
            },
        }
        persisted_state, created = self.store.create_publish_job(state)
        if not created:
            if persisted_state.get("idempotency_facts") != idempotency_facts:
                raise PublishIdempotencyConflictError()
            return self._enqueue_result(
                persisted_state,
                idempotent_replay=True,
            )

        self._submit_job(job_id, product, selected)
        self._update_job_status(job_id)
        return self._enqueue_result(state, idempotent_replay=False)

    def recover_publish_job(
        self,
        *,
        idempotency_key: str,
        product_id: str,
        draft_id: str,
        validation_digest: str,
        platform: str,
        site: str,
    ) -> dict[str, Any] | None:
        """在重校验/准入前，按完整规范化确认事实恢复已有任务。"""

        trusted_key = str(idempotency_key or "").strip()
        expected_product = str(product_id or "").strip()
        expected_draft = str(draft_id or "").strip()
        expected_digest = str(validation_digest or "").strip().lower()
        expected_platform = str(platform or "").strip().lower()
        expected_site = str(site or "").strip().lower()
        if not all(
            (
                trusted_key,
                expected_product,
                expected_draft,
                expected_digest,
                expected_platform,
                expected_site,
            )
        ):
            raise ValueError("发布任务恢复缺少完整可信事实。")

        state = self.store.load_publish_job_by_idempotency_key(trusted_key)
        if not state:
            return None
        if str(state.get("idempotency_key") or "").strip() != trusted_key:
            raise PublishIdempotencyConflictError()

        product = state.get("product") if isinstance(state.get("product"), dict) else {}
        persisted_product = str(product.get("product_id") or "").strip()
        raw_platforms = state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
        selected = sorted(
            {
                str(item or "").strip().lower()
                for item in raw_platforms
                if str(item or "").strip()
            }
        )
        if selected != [expected_platform]:
            raise PublishIdempotencyConflictError()
        raw_target = raw_platforms.get(expected_platform)
        if not isinstance(raw_target, dict):
            raise PublishIdempotencyConflictError()
        bindings = {
            expected_platform: {
                "draft_id": str(raw_target.get("draft_id") or "").strip(),
                "site": str(raw_target.get("site") or "").strip(),
                "product_id": str(raw_target.get("product_id") or "").strip(),
            }
        }
        try:
            approvals = self._normalize_approved_publications(
                state.get("approved_publications"),
                selected,
            )
        except ValueError as exc:
            raise PublishIdempotencyConflictError() from exc
        if not approvals:
            raise PublishIdempotencyConflictError()
        try:
            self._validate_approved_publications(
                product_id=persisted_product,
                bindings=bindings,
                approved_publications=approvals,
            )
        except PublishApprovalBindingError as exc:
            raise PublishIdempotencyConflictError() from exc
        canonical_facts = self._build_idempotency_facts(
            product_id=persisted_product,
            platforms=selected,
            bindings=bindings,
            approved_publications=approvals,
        )
        approval = approvals[expected_platform]
        if (
            state.get("idempotency_facts") != canonical_facts
            or persisted_product != expected_product
            or bindings[expected_platform]["product_id"] != expected_product
            or bindings[expected_platform]["draft_id"] != expected_draft
            or bindings[expected_platform]["site"].strip().lower() != expected_site
            or str(approval.get("validation_digest") or "").strip().lower()
            != expected_digest
        ):
            raise PublishIdempotencyConflictError()
        return self._enqueue_result(state, idempotent_replay=True)

    @staticmethod
    def _normalize_approved_publications(
        raw_approvals: Any,
        platforms: list[str],
    ) -> dict[str, dict[str, Any]]:
        if raw_approvals in (None, {}):
            return {}
        if not isinstance(raw_approvals, dict):
            raise ValueError("发布确认 payload 必须是平台映射。")
        selected = sorted(platforms)
        normalized_keys = sorted(
            str(key or "").strip().lower() for key in raw_approvals
        )
        if normalized_keys != selected:
            raise ValueError("发布确认 payload 必须精确覆盖入队平台。")
        approvals: dict[str, dict[str, Any]] = {}
        for raw_platform, raw_approval in raw_approvals.items():
            platform = str(raw_platform or "").strip().lower()
            if not isinstance(raw_approval, dict):
                raise ValueError(f"{platform} 发布确认不是对象。")
            payload = raw_approval.get("payload")
            digest = str(raw_approval.get("validation_digest") or "").strip().lower()
            store_identity = str(raw_approval.get("store_identity") or "").strip()
            if not isinstance(payload, dict):
                raise ValueError(f"{platform} 发布确认缺少 payload。")
            if (
                len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError(f"{platform} 发布确认 digest 无效。")
            if not store_identity.startswith(f"{platform}:"):
                raise ValueError(f"{platform} 发布确认的店铺身份无效。")
            approvals[platform] = {
                "payload": copy.deepcopy(payload),
                "validation_digest": digest,
                "store_identity": store_identity,
            }
        return approvals

    @staticmethod
    def _build_idempotency_facts(
        *,
        product_id: str,
        platforms: list[str],
        bindings: dict[str, dict[str, str]],
        approved_publications: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        selected = sorted(str(item or "").strip().lower() for item in platforms)
        facts: dict[str, Any] = {
            "product_id": str(product_id or "").strip(),
            "platforms": selected,
            "targets": {
                platform: {
                    "draft_id": str(bindings[platform].get("draft_id") or "").strip(),
                    "site": str(bindings[platform].get("site") or "").strip().lower(),
                    "product_id": str(bindings[platform].get("product_id") or "").strip(),
                }
                for platform in selected
            },
        }
        if approved_publications:
            facts["confirmation_digests"] = {
                platform: str(
                    approved_publications[platform].get("validation_digest") or ""
                ).strip().lower()
                for platform in selected
            }
        return facts

    @staticmethod
    def _validate_approved_publications(
        *,
        product_id: str,
        bindings: dict[str, dict[str, str]],
        approved_publications: dict[str, dict[str, Any]],
    ) -> None:
        for platform, approval in approved_publications.items():
            binding = bindings.get(platform) or {}
            payload = approval.get("payload")
            if not isinstance(payload, dict):
                raise PublishApprovalBindingError(
                    f"{platform} 发布确认缺少 payload。"
                )
            actual_digest = canonical_publish_digest(
                product_id=product_id,
                draft_id=str(binding.get("draft_id") or ""),
                platform=platform,
                site=str(binding.get("site") or ""),
                store_identity=str(approval.get("store_identity") or ""),
                payload=payload,
            )
            expected_digest = str(
                approval.get("validation_digest") or ""
            ).strip().lower()
            if not hmac.compare_digest(actual_digest, expected_digest):
                raise PublishApprovalBindingError(
                    f"{platform} 发布确认 digest 与 payload/目标不一致。"
                )

    @staticmethod
    def _enqueue_result(
        state: dict[str, Any],
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        raw_platforms = (
            state.get("platforms")
            if isinstance(state.get("platforms"), dict)
            else {}
        )
        platforms = sorted(str(platform) for platform in raw_platforms)
        targets = {
            platform: {
                "draft_id": str(raw_platforms[platform].get("draft_id") or ""),
                "site": str(raw_platforms[platform].get("site") or ""),
                "product_id": str(raw_platforms[platform].get("product_id") or ""),
            }
            for platform in platforms
            if isinstance(raw_platforms.get(platform), dict)
        }
        return {
            "ok": True,
            "job_id": str(state.get("job_id") or ""),
            "platforms": platforms,
            "targets": targets,
            "status": str(state.get("status") or "queued"),
            "idempotent_replay": idempotent_replay,
        }

    def recover_pending_jobs(self) -> list[str]:
        recovered: list[str] = []
        for state in self.store.list_pending_publish_jobs():
            if not isinstance(state, dict):
                continue
            job_id = str(state.get("job_id") or "")
            if not job_id:
                continue
            if (
                str(state.get("status") or "").strip().lower()
                in {"completed", OUTCOME_UNKNOWN_JOB_STATUS}
                and not state.get("terminal_results_persisted")
            ):
                # Job status is committed before the terminal callback runs。
                # completed/outcome_unknown 都必须在重启后补偿持久化草稿与日志。
                if self.terminal_callback is None:
                    continue
                self._update_job_status(job_id)
                if self._read_state(job_id).get(
                    "terminal_results_persisted"
                ):
                    recovered.append(job_id)
                continue
            if self._resume_state(job_id, state):
                recovered.append(job_id)
        return recovered

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        deadline = time.time() + timeout if timeout else None
        for future in list(self._futures.get(job_id, [])):
            remaining = None if deadline is None else max(0.0, deadline - time.time())
            future.result(timeout=remaining)
        self._update_job_status(job_id)

    def get_status(self, job_id: str) -> dict[str, Any]:
        return self._read_state(job_id)

    def get_public_status(self, job_id: str) -> dict[str, Any]:
        state = copy.deepcopy(self._read_state(job_id))
        summary = _publish_job_summary(state)
        state.pop("product", None)
        state.pop("idempotency_key", None)
        state.pop("idempotency_facts", None)
        state.pop("approved_publications", None)
        state["product_id"] = summary["product_id"]
        state["product_name"] = summary["product_name"]
        state["draft_id"] = summary["draft_id"]
        state["display_status"] = summary["status"]
        platforms = state.get("platforms")
        if isinstance(platforms, dict):
            for item in platforms.values():
                if not isinstance(item, dict):
                    continue
                result = item.get("result")
                if isinstance(result, dict):
                    result.pop("product", None)
                    result.pop("payload", None)
                    result.pop("approved_payload", None)
                    result.pop("validation_digest", None)
                    result.pop("store_identity", None)
        return state

    def reconcile_outcome_unknown(
        self,
        job_id: str,
        platform: str,
    ) -> dict[str, Any]:
        """只读确认一个 ``outcome_unknown`` 平台结果。

        该入口只允许复用已经持久化的 task/result 调用适配器状态查询，绝不
        重放 publish mutation。确认到终态后才释放同草稿/平台的持久锁；仍在
        处理中或响应依旧不可验证时继续保持 ``outcome_unknown``。
        """

        resolved_job_id = str(job_id or "").strip()
        resolved_platform = str(platform or "").strip().lower()
        if not resolved_job_id or not resolved_platform:
            raise ValueError("发布结果对账缺少 job_id 或 platform。")
        adapter = self.adapters.get(resolved_platform)
        if adapter is None:
            raise ValueError(f"发布结果对账不支持平台：{resolved_platform}")
        poller = getattr(adapter, "poll_publish_status", None)
        if not callable(poller):
            raise ValueError(
                f"{resolved_platform} 发布适配器没有只读结果确认能力。"
            )

        reconcile_key = (resolved_job_id, resolved_platform)
        with self._lock:
            if reconcile_key in self._reconciling:
                raise RuntimeError("该发布结果正在对账，请勿并发提交。")
            state = self._read_state(resolved_job_id)
            platforms = (
                state.get("platforms")
                if isinstance(state.get("platforms"), dict)
                else {}
            )
            item = platforms.get(resolved_platform)
            if not isinstance(item, dict):
                raise ValueError(
                    f"发布任务不包含平台：{resolved_platform}"
                )
            if (
                str(item.get("status") or "").strip().lower()
                != OUTCOME_UNKNOWN_JOB_STATUS
            ):
                raise ValueError("只有结果待对账的平台任务可以执行对账。")
            persisted_result = item.get("result")
            if not isinstance(persisted_result, dict):
                raise ValueError("结果待对账任务没有可供只读确认的持久化结果。")
            has_task_identity = bool(
                str(persisted_result.get("task_id") or "").strip()
                or any(
                    str(task_id or "").strip()
                    for task_id in (
                        persisted_result.get("task_ids")
                        if isinstance(persisted_result.get("task_ids"), list)
                        else []
                    )
                )
            )
            if not has_task_identity:
                raise ValueError(
                    "该未知结果没有远端 task_id，无法自动对账；"
                    "必须先通过 Mercado 后台或支持渠道确认。"
                )
            self._reconciling.add(reconcile_key)

        checked_at = current_time()
        try:
            config = self.config_provider()
            config = config if isinstance(config, dict) else {}
            checked_result = poller(copy.deepcopy(persisted_result), config)
            if not isinstance(checked_result, dict):
                raise RuntimeError("平台对账没有返回可验证的 object 结果。")

            result_status = str(
                checked_result.get("status") or ""
            ).strip().lower()
            success_evidence = bool(
                checked_result.get("ok") is True
                and (
                    result_status
                    in {"published", "success", "real_publish_success"}
                    or checked_result.get("id") not in (None, "", 0)
                    or checked_result.get("item_id") not in (None, "", 0)
                    or checked_result.get("external_id")
                    not in (None, "", 0)
                )
            )
            pending = self._is_pending_publish_result(checked_result)
            deterministic_failure = result_status in {
                "failed",
                "partial",
                "not_ready",
                "ready_for_real_publish",
                "skipped",
            }
            resolved_status = (
                "success"
                if success_evidence
                else "failed"
                if deterministic_failure
                else OUTCOME_UNKNOWN_JOB_STATUS
            )
            resolution = (
                "applied"
                if success_evidence
                else "partially_applied"
                if result_status == "partial"
                else "not_applied"
                if deterministic_failure
                else "pending"
                if pending
                else "unconfirmed"
            )
            persisted_checked = self._persisted_platform_result(
                checked_result
            )

            with self._lock:
                latest = self._read_state(resolved_job_id)
                latest_platforms = (
                    latest.get("platforms")
                    if isinstance(latest.get("platforms"), dict)
                    else {}
                )
                latest_item = latest_platforms.get(resolved_platform)
                if not isinstance(latest_item, dict):
                    raise RuntimeError("对账期间发布平台状态已被移除。")
                if (
                    str(latest_item.get("status") or "").strip().lower()
                    != OUTCOME_UNKNOWN_JOB_STATUS
                ):
                    raise RuntimeError("对账期间发布平台状态已经改变。")
                latest_item.update(
                    {
                        "status": resolved_status,
                        "stage": (
                            "finished"
                            if success_evidence
                            else "failed"
                            if deterministic_failure
                            else OUTCOME_UNKNOWN_JOB_STATUS
                        ),
                        "error": (
                            ""
                            if success_evidence
                            else str(
                                checked_result.get("error")
                                or latest_item.get("error")
                                or "远端结果仍不可验证"
                            )
                        ),
                        "result": persisted_checked,
                        "reconciliation": {
                            "status": resolution,
                            "checked_at": checked_at,
                            "write_replayed": False,
                        },
                        "updated_at": checked_at,
                    }
                )
                latest["updated_at"] = checked_at
                if resolved_status != OUTCOME_UNKNOWN_JOB_STATUS:
                    # 初次 unknown 终态已经执行过一次 callback；最终确认后必须
                    # 再回写草稿。日志仍保留最初 unknown 审计，job 保存最终结论。
                    latest.pop("terminal_results_persisted", None)
                    latest.pop("terminal_persistence_error", None)
                self._write_state(resolved_job_id, latest)

            self._update_job_status(resolved_job_id)
            return {
                "ok": True,
                "job_id": resolved_job_id,
                "platform": resolved_platform,
                "resolved": resolved_status != OUTCOME_UNKNOWN_JOB_STATUS,
                "resolution": resolution,
                "job": self.get_public_status(resolved_job_id),
            }
        finally:
            with self._lock:
                self._reconciling.discard(reconcile_key)

    def list_jobs(
        self,
        *,
        limit: int = 50,
        cursor: str = "",
        status: str = "",
        platform: str = "",
        product_id: str = "",
    ) -> dict[str, Any]:
        resolved_limit = max(1, min(int(limit or 50), 100))
        status = str(status or "").strip().lower()
        scan_cursor = str(cursor or "").strip()
        summaries: list[dict[str, Any]] = []

        while len(summaries) <= resolved_limit:
            states, next_scan_cursor = self.store.list_publish_jobs(
                limit=100,
                cursor=scan_cursor,
                platform=str(platform or "").strip().lower(),
                product_id=str(product_id or "").strip(),
            )
            if not states:
                break
            for state in states:
                summary = _publish_job_summary(state)
                if status and summary["status"] != status:
                    continue
                summaries.append(summary)
                if len(summaries) > resolved_limit:
                    return {
                        "items": summaries[:resolved_limit],
                        "next_cursor": summaries[resolved_limit - 1]["job_id"],
                    }
            if not next_scan_cursor:
                break
            scan_cursor = next_scan_cursor

        return {"items": summaries[:resolved_limit], "next_cursor": ""}

    def _submit_job(self, job_id: str, product: dict[str, Any], platforms: list[str]) -> None:
        futures = [
            self.executor.submit(self._run_platform, job_id, copy.deepcopy(product), platform)
            for platform in platforms
        ]
        self._futures[job_id] = futures

    def _resume_state(self, job_id: str, state: dict[str, Any]) -> bool:
        product = state.get("product") if isinstance(state.get("product"), dict) else {}
        pending = []
        changed = False
        platforms = state.get("platforms") if isinstance(state.get("platforms"), dict) else {}
        for platform, item in platforms.items():
            if not isinstance(item, dict):
                continue
            status = str(item.get("status") or "").lower()
            if status in {"pending", "queued", "running", "retrying"} and platform in self.adapters:
                stage = str(item.get("stage") or "").strip().lower()
                result = item.get("result")
                if (
                    status in {"running", "retrying"}
                    and stage in {"publishing", "publishing_approved_payload"}
                    and not isinstance(result, dict)
                ):
                    # 进程可能恰好在远端写入成功后、结果持久化前退出。
                    # 自动重放会重复创建商品，因此保守进入人工对账状态。
                    error = (
                        "发布请求可能已送达平台；为避免重复创建，重启后未自动重放，"
                        "请先完成平台对账。"
                    )
                    item.update(
                        {
                            "status": OUTCOME_UNKNOWN_JOB_STATUS,
                            "stage": OUTCOME_UNKNOWN_JOB_STATUS,
                            "error": error,
                            "result": {
                                "ok": False,
                                "status": OUTCOME_UNKNOWN_JOB_STATUS,
                                "error_code": "PUBLISH_OUTCOME_UNKNOWN",
                                "error": error,
                                "outcome_unknown": True,
                            },
                            "updated_at": current_time(),
                        }
                    )
                    changed = True
                    continue
                item["status"] = "queued"
                item["stage"] = "resuming"
                item["error"] = str(item.get("error") or "")
                item["updated_at"] = current_time()
                pending.append(platform)
                changed = True
        if not changed:
            return False
        state["status"] = (
            "queued" if pending else OUTCOME_UNKNOWN_JOB_STATUS
        )
        state["updated_at"] = current_time()
        # 旧 job 状态里若残留 config/凭据，一律丢弃（发布执行时从 store_auth 现取）。
        state.pop("config", None)
        self._write_state(job_id, state)
        if pending:
            self._submit_job(job_id, product, pending)
        self._update_job_status(job_id)
        return True

    def _new_platform_state(
        self,
        platform: str,
        now: str,
        target: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = target if isinstance(target, dict) else {}
        return {
            "platform": platform,
            "draft_id": str(target.get("draft_id") or ""),
            "site": str(target.get("site") or ""),
            "product_id": str(target.get("product_id") or ""),
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "stage": "queued",
            "error": "",
            "result": None,
            "attempts": 0,
        }

    def _run_platform(self, job_id: str, product: dict[str, Any], platform: str) -> None:
        adapter = self.adapters[platform]
        attempts = 0
        max_attempts = self.max_retries + 1

        while attempts < max_attempts:
            attempts += 1
            stored_result: dict[str, Any] | None = None
            try:
                # 每次执行时现取店铺配置（凭据来自 store_auth 表），不落任何 job 持久化。
                config = self.config_provider()
                config = config if isinstance(config, dict) else {}
                state = self._read_state(job_id)
                platform_state = (
                    state.get("platforms", {}).get(platform)
                    if isinstance(state.get("platforms"), dict)
                    else {}
                )
                stored_result = (
                    platform_state.get("result")
                    if isinstance(platform_state, dict)
                    and isinstance(platform_state.get("result"), dict)
                    else None
                )
                approvals = (
                    state.get("approved_publications")
                    if isinstance(state.get("approved_publications"), dict)
                    else {}
                )
                approval = (
                    approvals.get(platform)
                    if isinstance(approvals.get(platform), dict)
                    else None
                )
                if self._is_pending_publish_result(stored_result):
                    if approval is not None:
                        self._approved_payload_for_worker(
                            platform=platform,
                            state=state,
                            approval=approval,
                            adapter=adapter,
                            config=config,
                        )
                    result: Any = stored_result
                    self._set_platform(
                        job_id,
                        platform,
                        status="running",
                        stage="waiting_platform_confirmation",
                        error="",
                        attempts=attempts,
                    )
                else:
                    if approval is not None:
                        result = self._publish_approved_payload(
                            job_id=job_id,
                            platform=platform,
                            state=state,
                            approval=approval,
                            adapter=adapter,
                            config=config,
                            attempts=attempts,
                        )
                    else:
                        self._set_platform(job_id, platform, status="running", stage="resolving_category", attempts=attempts)
                        resolved = adapter.resolve_category(product, config)
                        product = resolved if isinstance(resolved, dict) else product
                        drafts = (
                            product.get("drafts")
                            if isinstance(product.get("drafts"), dict)
                            else {}
                        )
                        draft = (
                            drafts.get(platform)
                            if isinstance(drafts.get(platform), dict)
                            else {}
                        )
                        self._set_platform(
                            job_id,
                            platform,
                            stage="validating_required_attributes",
                            category_id=str(
                                draft.get("category_id")
                                or ""
                            ),
                            attempts=attempts,
                        )
                        missing = adapter.required_attributes_missing(
                            prepare_publish_context(product, platform),
                            config,
                        )
                        if missing:
                            self._set_platform(
                                job_id,
                                platform,
                                status="failed",
                                stage="failed",
                                error="缺失必填属性：" + "，".join(str(item) for item in missing),
                                attempts=attempts,
                            )
                            return
                        self._set_platform(job_id, platform, stage="publishing", attempts=attempts)
                        result = adapter.publish(product, platform, config)

                while self._is_pending_publish_result(result):
                    poller = getattr(adapter, "poll_publish_status", None)
                    if not callable(poller):
                        raise RuntimeError(
                            f"{platform} 返回待确认状态，但发布适配器未实现状态轮询"
                        )
                    persisted_pending = self._persisted_platform_result(result)
                    # 供异常分支区分“只读确认失败”与“首次写请求失败”；前者
                    # 可安全重试 GET，后者绝不能重放 mutation。
                    stored_result = persisted_pending
                    self._set_platform(
                        job_id,
                        platform,
                        status="running",
                        stage="waiting_platform_confirmation",
                        error="",
                        result=persisted_pending,
                        attempts=attempts,
                    )
                    interval_provider = getattr(
                        adapter,
                        "publish_poll_interval_seconds",
                        None,
                    )
                    interval = (
                        interval_provider(config)
                        if callable(interval_provider)
                        else self.retry_delay_seconds
                    )
                    time.sleep(max(0.05, float(interval)))
                    result = poller(result, config)

                persisted_result = (
                    self._persisted_platform_result(result)
                )
                result_status = (
                    str(result.get("status") or "").strip().lower()
                    if isinstance(result, dict)
                    else ""
                )
                success_evidence = (
                    isinstance(result, dict)
                    and result.get("ok") is True
                    and (
                        result_status
                        in {
                            "published",
                            "success",
                            "real_publish_success",
                        }
                        or bool(
                            result.get("id")
                            or result.get("item_id")
                            or result.get("external_id")
                        )
                    )
                )
                if not success_evidence:
                    failure_status = (
                        result_status
                        if result_status
                        in {
                            "failed",
                            "not_ready",
                            "ready_for_real_publish",
                            "skipped",
                            OUTCOME_UNKNOWN_JOB_STATUS,
                        }
                        else "failed"
                    )
                    error = (
                        str(result.get("error") or "")
                        if isinstance(result, dict)
                        else ""
                    )
                    self._set_platform(
                        job_id,
                        platform,
                        status=failure_status,
                        stage=failure_status,
                        error=error
                        or "发布适配器未返回可验证的成功结果",
                        result=persisted_result,
                        attempts=attempts,
                    )
                    return
                self._set_platform(
                    job_id,
                    platform,
                    status="success",
                    stage="finished",
                    result=persisted_result,
                    attempts=attempts,
                )
                return
            except Exception as exc:
                outcome_unknown = bool(
                    isinstance(exc, PublishAdapterError)
                    and exc.details.get("outcome_unknown") is True
                )
                confirmation_read = self._is_pending_publish_result(
                    stored_result
                )
                retryable = bool(
                    _publish_exception_retryable(exc)
                    and attempts < max_attempts
                    and (not outcome_unknown or confirmation_read)
                )
                if outcome_unknown and not retryable:
                    safe_details = {
                        key: exc.details[key]
                        for key in (
                            "http_status",
                            "remote_write_dispatched",
                            "outcome_unknown",
                        )
                        if key in exc.details
                    }
                    self._set_platform(
                        job_id,
                        platform,
                        status=OUTCOME_UNKNOWN_JOB_STATUS,
                        stage=OUTCOME_UNKNOWN_JOB_STATUS,
                        error=str(exc),
                        result={
                            **(
                                copy.deepcopy(stored_result)
                                if confirmation_read
                                and isinstance(stored_result, dict)
                                else {}
                            ),
                            "ok": False,
                            "status": OUTCOME_UNKNOWN_JOB_STATUS,
                            "error_code": exc.code,
                            "error": str(exc),
                            "outcome_unknown": True,
                            "details": safe_details,
                        },
                        attempts=attempts,
                    )
                    return
                self._set_platform(
                    job_id,
                    platform,
                    status="retrying" if retryable else "failed",
                    stage="retrying" if retryable else "failed",
                    error=str(exc),
                    attempts=attempts,
                )
                if not retryable:
                    return
                time.sleep(self.retry_delay_seconds)
        self._update_job_status(job_id)

    def _publish_approved_payload(
        self,
        *,
        job_id: str,
        platform: str,
        state: dict[str, Any],
        approval: dict[str, Any],
        adapter: PublishingAdapter,
        config: dict[str, Any],
        attempts: int,
    ) -> dict[str, Any]:
        payload = self._approved_payload_for_worker(
            platform=platform,
            state=state,
            approval=approval,
            adapter=adapter,
            config=config,
        )
        self._set_platform(
            job_id,
            platform,
            status="running",
            stage="publishing_approved_payload",
            error="",
            attempts=attempts,
        )
        return adapter.publish_payload(copy.deepcopy(payload), config)

    @staticmethod
    def _approved_payload_for_worker(
        *,
        platform: str,
        state: dict[str, Any],
        approval: dict[str, Any],
        adapter: PublishingAdapter,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        payload = approval.get("payload")
        if not isinstance(payload, dict):
            raise PublishApprovalBindingError(
                "发布确认缺少已批准 payload，已阻止外发。"
            )
        expected_digest = str(
            approval.get("validation_digest") or ""
        ).strip().lower()
        expected_identity = str(
            approval.get("store_identity") or ""
        ).strip()
        try:
            current_binding = resolve_publish_store_binding(platform, config)
        except ValueError as exc:
            raise PublishApprovalBindingError(
                "当前店铺缺少稳定账号身份，已阻止使用旧确认外发。"
            ) from exc
        if not hmac.compare_digest(current_binding.identity, expected_identity):
            raise PublishApprovalBindingError(
                "当前店铺账号与人工确认绑定的账号不一致，已阻止外发。"
            )

        product = (
            state.get("product")
            if isinstance(state.get("product"), dict)
            else {}
        )
        platform_state = (
            state.get("platforms", {}).get(platform)
            if isinstance(state.get("platforms"), dict)
            else {}
        )
        actual_digest = canonical_publish_digest(
            product_id=str(product.get("product_id") or ""),
            draft_id=str(platform_state.get("draft_id") or ""),
            platform=platform,
            site=str(platform_state.get("site") or ""),
            store_identity=current_binding.identity,
            payload=payload,
        )
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise PublishApprovalBindingError(
                "已批准 payload、目标或店铺身份与确认 digest 不一致，已阻止外发。"
            )
        payload_errors = adapter.validate_payload(
            copy.deepcopy(payload),
            copy.deepcopy(config),
        ) or []
        if payload_errors:
            raise PublishApprovalBindingError(
                "已批准 payload 在当前店铺配置下无效："
                + "，".join(str(item) for item in payload_errors)
            )
        return copy.deepcopy(payload)

    @staticmethod
    def _is_pending_publish_result(result: Any) -> bool:
        return bool(
            isinstance(result, dict)
            and str(result.get("status") or "").strip().lower()
            in {"pending_confirmation", "publish_pending_confirmation"}
        )

    @staticmethod
    def _persisted_platform_result(result: Any) -> dict[str, Any] | None:
        persisted = copy.deepcopy(result) if isinstance(result, dict) else None
        if isinstance(persisted, dict):
            # job 根节点已经保存恢复执行所需的 product；平台 result
            # 再存一次只会让 SQLite 与发布日志成倍膨胀。
            persisted.pop("product", None)
        return persisted

    def _set_platform(self, job_id: str, platform: str, **updates: Any) -> None:
        with self._lock:
            state = self._read_state(job_id)
            item = state["platforms"].setdefault(platform, self._new_platform_state(platform, current_time()))
            item.update(updates)
            item["updated_at"] = current_time()
            state["updated_at"] = item["updated_at"]
            self._write_state(job_id, state)
        if str(updates.get("status") or "").lower() in {"success", "failed", "not_ready", "ready_for_real_publish", "skipped", OUTCOME_UNKNOWN_JOB_STATUS}:
            self._update_job_status(job_id)

    def _update_job_status(self, job_id: str) -> None:
        with self._lock:
            state = self._read_state(job_id)
            statuses = [str(item.get("status") or "").lower() for item in state.get("platforms", {}).values()]
            if any(status in {"running", "retrying"} for status in statuses):
                state["status"] = "running"
            elif OUTCOME_UNKNOWN_JOB_STATUS in statuses:
                state["status"] = OUTCOME_UNKNOWN_JOB_STATUS
            elif statuses and all(status in {"success", "failed", "not_ready", "ready_for_real_publish", "skipped"} for status in statuses):
                state["status"] = "completed"
            else:
                state["status"] = "queued"
            state["updated_at"] = current_time()
            self._write_state(job_id, state)
            if (
                state["status"] in {"completed", OUTCOME_UNKNOWN_JOB_STATUS}
                and self.terminal_callback is not None
                and not state.get("terminal_results_persisted")
            ):
                try:
                    persisted = self.terminal_callback(
                        copy.deepcopy(state)
                    )
                    if isinstance(persisted, dict):
                        state = persisted
                    state["terminal_results_persisted"] = True
                    state.pop("terminal_persistence_error", None)
                except Exception as exc:
                    state["terminal_persistence_error"] = str(exc)
                state["updated_at"] = current_time()
                self._write_state(job_id, state)

    def _read_state(self, job_id: str) -> dict[str, Any]:
        state = self.store.load_publish_job(job_id)
        if not state:
            raise FileNotFoundError(f"发布任务不存在：{job_id}")
        return state

    def _write_state(self, job_id: str, state: dict[str, Any]) -> None:
        state["job_id"] = str(state.get("job_id") or job_id)
        self.store.save_publish_job(state)


def current_time() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

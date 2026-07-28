from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy

from erp_web.context import get_context
from erp_web.http_route_units import get_routes
from erp_web.product_model import normalize_product_model
from erp_web.runtime_units.publish_adapter import publishing_adapter_for
from erp_web.runtime_units.publishing_bus_core import PublishingBus
from erp_web.schemas.api import API_SCHEMA_VERSION

from .helpers import (
    ROOT,
    format_findings,
    imported_targets,
    python_files,
    read,
    unvalidated_body_reads,
)


# 验收：生产代码必须显式导入具体模块，任何层都不得重新依赖 erp_web.runtime 聚合器。
def test_production_code_does_not_import_runtime_aggregator() -> None:
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(python_files("erp_web"))
        if (
            target.lstrip(".") == "runtime"
            or target.lstrip(".").startswith("runtime.")
            or target == "erp_web.runtime"
            or target.startswith("erp_web.runtime.")
            or target.endswith(".runtime")
        )
    ]
    assert not offenders, f"仍依赖 runtime 聚合器：{offenders}"


# 验收：旧产品可以迁移读取，但版本一模型写出后不得继续携带历史别名投影。
def test_product_migration_reads_old_and_writes_only_schema_v1() -> None:
    legacy = {
        "id": "legacy-id",
        "title": "旧标题",
        "source_images": ["legacy-a.jpg"],
        "source_image_urls": ["legacy-b.jpg"],
        "detail_images": ["legacy-c.jpg"],
        "detected_price": "12.34",
        "detected_currency": "CNY",
        "gtin": "123",
        "barcode": "123",
        "category_id": "MLM-LEGACY",
        "source_url": "https://example.invalid/item",
        "source_platform": "1688",
    }
    migrated = normalize_product_model(legacy)
    forbidden = {
        "id",
        "title",
        "source_images",
        "source_image_urls",
        "detail_images",
        "detail_image_urls",
        "gtin",
        "barcode",
        "detected_price",
        "detected_currency",
    }
    assert migrated["schema_version"] == 1
    assert migrated.get("product_id") == "legacy-id"
    assert migrated.get("name") == "旧标题"
    assert migrated.get("upc") == "123"
    source = migrated.get("source")
    assert isinstance(source, dict)
    assert source.get("source_url") == "https://example.invalid/item"
    assert source.get("source_platform") == "1688"
    assert source.get("price") == "12.34"
    assert source.get("currency") == "CNY"
    image_refs = {
        str(item.get("url") or item.get("path") or "")
        for item in source.get("image_pool", [])
        if isinstance(item, dict)
    }
    assert {"legacy-a.jpg", "legacy-b.jpg", "legacy-c.jpg"}.issubset(image_refs)
    drafts = migrated.get("drafts")
    assert isinstance(drafts, dict)
    assert drafts.get("mercadolibre", {}).get("category_id") == "MLM-LEGACY"
    assert forbidden.isdisjoint(migrated), f"版本一仍写出旧字段：{sorted(forbidden & migrated.keys())}"
    with get_context().db._connect() as connection:
        before_count = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]
    assert before_count == 0
    saved = get_context().products.save_product(legacy)
    with get_context().db._connect() as connection:
        persisted = json.loads(
            connection.execute(
                "SELECT product_json FROM products WHERE product_id = ?",
                (saved["product_id"],),
            ).fetchone()[0]
        )
    assert persisted["schema_version"] == 1
    assert forbidden.isdisjoint(persisted)


# 验收：前端工作流类型必须由后端 schema 生成，而不是继续人工维护双向 snake/camel 兜底。
def test_frontend_workflow_types_are_generated_from_schema() -> None:
    generator = ROOT / "scripts/generate_frontend_types.py"
    assert generator.exists(), "缺少稳定的前端类型生成入口 scripts/generate_frontend_types.py"
    result = subprocess.run(
        [sys.executable, str(generator), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        "front/src/types/workflow.ts 与后端 schema 不一致；"
        "请重新生成类型。\n"
        f"{result.stdout}\n{result.stderr}"
    )


# 验收：依赖方向必须单向，services 不得反向导入 runtime_units。
def test_services_do_not_import_runtime_units() -> None:
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(python_files("erp_web/services"))
        if "runtime_units" in target
    ]
    assert not offenders, "services 反向依赖 runtime_units：\n" + "\n".join(offenders)


# 验收：HTTP route 只能调用 facade/domain service，不得直接编排 runtime_units 业务函数。
def test_http_routes_do_not_import_business_runtime_units() -> None:
    allowed = {"..runtime_units.runtime_api", "erp_web.runtime_units.runtime_api"}
    route_files = sorted(
        (ROOT / "erp_web/http_route_units").rglob("*_routes.py")
    )
    assert route_files, "没有发现任何 HTTP route unit"
    offenders = [
        f"{path.relative_to(ROOT)} -> {target}"
        for path, target in imported_targets(route_files)
        if "runtime_units" in target
        and not any(target == item or target.startswith(f"{item}.") for item in allowed)
    ]
    assert not offenders, "route 仍直接依赖业务 runtime unit：\n" + "\n".join(offenders)


def _state_response(monkeypatch, tmp_path) -> dict:
    sent: list[dict] = []
    app_secret = "state-ai-secret"
    cookie_secret = "state-cookie-secret"
    store_secret = "state-store-secret"
    product = normalize_product_model(
        {
            "name": "State contract product",
            "source": {
                "title": "State contract product",
                "source_url": "https://example.invalid/state",
                "image_pool": [],
            },
        }
    )

    class Handler:
        @staticmethod
        def send_json(payload: dict, status: int = 200) -> None:
            assert status == 200
            sent.append(payload)

    monkeypatch.setattr(get_routes, "APP_DIR", tmp_path)
    monkeypatch.setattr(get_routes, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(get_routes, "load_product", lambda: product)
    monkeypatch.setattr(
        get_routes,
        "load_app_config",
        lambda: {
            "auto_ai_recognition": "state-known-value",
            "alibaba_cookie": cookie_secret,
            "ai_models": [{"id": "state-model", "api_key": app_secret}],
        },
    )
    monkeypatch.setattr(
        get_routes,
        "load_store_config",
        lambda: {
            "mercadolibre": {
                "site_id": "MLM",
                "access_token": store_secret,
            }
        },
    )
    monkeypatch.setattr(get_routes, "current_generated_images", lambda: [])
    monkeypatch.setattr(get_routes, "load_publish_logs", lambda: [])
    monkeypatch.setattr(get_routes, "load_mercadolibre_order_notifications", lambda: [])
    monkeypatch.setattr(get_routes, "load_products_index", lambda: [])
    monkeypatch.setattr(get_routes, "load_drafts_index", lambda *args, **kwargs: [])
    monkeypatch.setattr(get_routes, "marketplace_options", lambda: [])

    get_routes.handle_state(Handler(), object())
    assert len(sent) == 1
    serialized = json.dumps(sent[0], ensure_ascii=False)
    for secret in (app_secret, cookie_secret, store_secret):
        assert secret not in serialized
    return sent[0]


# 验收：状态接口必须版本化、运行时校验并通过唯一公共脱敏出口返回配置。
def test_state_endpoint_is_versioned_validated_and_redacted(monkeypatch, tmp_path) -> None:
    state = _state_response(monkeypatch, tmp_path)
    assert state["schemaVersion"] == API_SCHEMA_VERSION
    assert state["ok"] is True
    assert isinstance(state["appConfig"], dict)
    assert isinstance(state["storeConfig"], dict)
    assert state["appConfig"]["auto_ai_recognition"] == "state-known-value"
    assert state["storeConfig"]["mercadolibre"]["site_id"] == "MLM"


# 验收：所有写接口都必须经过运行时 schema 验证，不能直接消费未经约束的 read_body 字典。
def test_write_endpoints_use_runtime_schema_validation() -> None:
    route_files = sorted(
        (ROOT / "erp_web/http_route_units").rglob("*_routes.py")
    )
    assert route_files, "没有发现任何 HTTP route unit"
    offenders = unvalidated_body_reads(route_files)
    assert not offenders, (
        "以下写入口读取 body 后没有在同一入口调用运行时 schema 校验器：\n"
        + format_findings(offenders)
    )


# 验收：workflow 主 store 和 API 必须按领域拆分，单文件不得继续充当上帝模块。
def test_frontend_workflow_modules_are_focused() -> None:
    workflow_store = read("front/src/stores/workflow.ts")
    workflow_api = read("front/src/api/workflow.ts")
    normalizers = read("front/src/api/workflow/normalizers.ts")
    domain_store_dir = ROOT / "front/src/stores/workflow"
    domain_stores = sorted(domain_store_dir.glob("*.ts"))
    required_domains = {
        "activity",
        "catalog",
        "collection",
        "publishing",
        "settings",
    }
    assert required_domains.issubset({path.stem for path in domain_stores})
    assert len(workflow_store.splitlines()) < 800
    assert len(workflow_api.splitlines()) < 800
    assert len(normalizers.splitlines()) < 800
    assert all(len(path.read_text(encoding="utf-8").splitlines()) < 800 for path in domain_stores)


# 验收：/api/state 必须拆掉索引、日志等大包字段，让各领域 store 独立加载自己的数据。
def test_state_endpoint_is_not_a_god_payload(monkeypatch, tmp_path) -> None:
    state = _state_response(monkeypatch, tmp_path)
    forbidden = {
        "productsIndex",
        "draftsIndex",
        "publishLogs",
        "mercadolibreOrderNotifications",
    }
    assert forbidden.isdisjoint(state), f"/api/state 仍包含领域大包字段：{sorted(forbidden & state.keys())}"


# 验收：本机单用户模式不得保留应用用户 token 管道，店铺 OAuth 代码必须使用 store_credentials 命名。
def test_fake_app_auth_is_removed_and_store_credentials_are_named_clearly() -> None:
    client = read("front/src/api/client.ts")
    meta = read("front/src/router/meta.d.ts")
    assert "accessToken" not in client
    assert "refreshToken" not in client
    assert "Authorization" not in client
    assert "requiresAuth" not in meta
    assert not (ROOT / "front/src/stores/auth.ts").exists()
    assert not (ROOT / "erp_web/runtime_units/auth_runtime.py").exists()
    assert (ROOT / "erp_web/runtime_units/store_credentials.py").exists()


# 验收：工作台只能挂载一个真实组件路由，其余历史入口必须重定向到 ?tab=。
def test_workflow_has_one_real_route_and_query_tabs() -> None:
    router = read("front/src/router/index.ts")
    view = read("front/src/views/workflow/WorkflowView.vue")
    assert router.count("component: workflowComponent") == 1
    assert "legacyWorkflowEntries" in router
    assert "query:" in router and "tab:" in router
    assert "route.query.tab" in view


# 验收：发布总线必须调用真实必填属性校验并在缺失字段时阻断发布。
def test_publish_queue_has_real_required_attribute_gate() -> None:
    real_adapter = publishing_adapter_for("mercadolibre")
    assert real_adapter is not None
    missing = real_adapter.required_attributes_missing(
        {
            "drafts": {
                "mercadolibre": {
                    "category_id": "MLM-ACCEPTANCE",
                    "attributes": {},
                }
            },
            "local_platform_categories": {
                "mercadolibre": {
                    "category_id": "MLM-ACCEPTANCE",
                    "attributes": {
                        "required": [
                            {
                                "id": "ACCEPTANCE_REQUIRED_ATTRIBUTE",
                                "name": "Required acceptance attribute",
                                "required": True,
                            }
                        ]
                    },
                }
            },
        },
        {},
    )
    assert missing == ["attributes.ACCEPTANCE_REQUIRED_ATTRIBUTE"]

    class MemoryStore:
        def __init__(self) -> None:
            self.states: dict[str, dict] = {}

        def save_publish_job(self, state: dict) -> None:
            self.states[str(state["job_id"])] = deepcopy(state)

        def load_publish_job(self, job_id: str) -> dict:
            return deepcopy(self.states.get(job_id, {}))

        @staticmethod
        def list_pending_publish_jobs() -> list[dict]:
            return []

    class MissingAttributeAdapter:
        publish_calls = 0

        @staticmethod
        def resolve_category(product: dict, config: dict) -> dict:
            return product

        @staticmethod
        def required_attributes_missing(product: dict, config: dict) -> list[str]:
            return ["attributes.BRAND"]

        def publish(self, product: dict, platform: str, config: dict) -> dict:
            self.publish_calls += 1
            return {"ok": True}

    store = MemoryStore()
    adapter = MissingAttributeAdapter()
    bus = PublishingBus(
        store,
        {"mercadolibre": adapter},
        config_provider=lambda: {"mercadolibre": {}},
        max_retries=0,
        auto_resume_pending=False,
    )
    try:
        queued = bus.enqueue({"name": "缺属性商品"}, ["mercadolibre"])
        bus.wait(queued["job_id"], timeout=2)
        state = bus.get_status(queued["job_id"])
    finally:
        bus.executor.shutdown(wait=True)
    assert state["platforms"]["mercadolibre"]["status"] == "failed"
    assert state["platforms"]["mercadolibre"]["error"] == "缺失必填属性：attributes.BRAND"
    assert adapter.publish_calls == 0


# 验收：设置保存必须使用顶层白名单合并，不能恢复 app_cfg.update 的 mass-assignment。
def test_app_config_updates_are_whitelisted() -> None:
    store = get_context().config
    current = store.default_app_config()
    current.setdefault("1688_api", {})["app_key"] = "keep-existing"
    merged = store.merge_app_config_fields(
        current,
        {
            "unknown_admin_override": True,
            "1688_api": {"timeout_seconds": 17},
        },
    )
    assert "unknown_admin_override" not in merged
    assert merged["1688_api"]["app_key"] == "keep-existing"
    assert merged["1688_api"]["timeout_seconds"] == 17


# 验收：根目录死包必须删除，且架构测试要显式阻止这些目录重新出现。
def test_dead_root_packages_are_deleted_and_guarded() -> None:
    dead_packages = ("routes", "services", "product_model_units", "marketplace_publish_units")
    assert all(not (ROOT / package).exists() for package in dead_packages)

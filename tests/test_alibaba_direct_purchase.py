"""1688 万邦个人直购测试：强制走万邦通道、多 SKU 下单、双地址模式、额度入账与 ERP 彻底隔离。"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from erp_web.context import AppContext, AppPaths, bind_context
from erp_web.db import ErpDatabase
from erp_web.facades import alibaba_direct_purchase_facade
from erp_web.schemas.alibaba_provider import BuyerOrder, PreviewGroup, PreviewItem
from erp_web.services.alibaba_api_errors import AlibabaApiError
from erp_web.services.alibaba_authorization_service import AlibabaAuthorizationService
from erp_web.services.alibaba_credentials import application_fingerprint, onebound_buyer_scope
from erp_web.services.onebound_quota_service import key_fingerprint
from erp_web.stores.config_store import ConfigStore
from tests.test_accounts import accounts_http, register  # noqa: F401


def _make_context(tmp_path: Path, *, shared_config: ConfigStore | None = None, shared_paths: AppPaths | None = None) -> AppContext:
    paths = AppPaths.from_app_dir(tmp_path)
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(paths=paths, db=ErpDatabase(paths.db_path))
    if shared_paths is not None:
        ctx.shared_paths = shared_paths
    if shared_config is not None:
        ctx.config.shared_config = shared_config
    return ctx


def _bind_onebound_session(
    root_ctx: AppContext,
    user_ctx: AppContext,
    *,
    api_key: str = "ob-key-direct",
    member_id: str = "b2b-direct-1",
    account_name: str = "直购买家A",
    credential_type: str = "access_token",
    directory_verified: bool | None = None,
) -> None:
    app = root_ctx.config.alibaba_application("onebound")
    auth = {
        "access_token": f"buyer-token-{member_id}",
        "credential_type": credential_type,
        "member_id": member_id,
        "account_name": account_name,
        "account_scope": onebound_buyer_scope(api_key, member_id),
        "application_fingerprint": application_fingerprint(app),
        "expires_at": 0,
    }
    if directory_verified is not None:
        auth["directory_verified"] = directory_verified
    user_ctx.config.update_alibaba_credentials(
        {
            "authorization": auth,
            "pending": {},
        },
        provider="onebound",
    )


def _setup_onebound_app_and_session(
    root_ctx: AppContext,
    user_ctx: AppContext,
    *,
    api_key: str = "ob-key-direct",
    api_secret: str = "ob-secret-direct",
    member_id: str = "b2b-direct-1",
    account_name: str = "直购买家A",
    active_admin_provider: str = "alibaba",
) -> None:
    current = root_ctx.config.managed_settings()
    root_ctx.config.save_managed_settings(
        {
            "revision": current["revision"],
            "exchange_rates": current["exchange_rates"],
            "alibaba_api_application": active_admin_provider,
            "alibaba": {
                "app_key": "111222",
                "app_secret": "self-secret",
                "redirect_uri": "https://erp.example.com/auth/alibaba/callback",
            },
            "alibaba_crossborder": {
                "app_key": "",
                "app_secret": "",
                "redirect_uri": "",
            },
            "onebound": {
                "api_key": api_key,
                "api_secret": api_secret,
                "initial_grant": 200,
            },
        }
    )
    _bind_onebound_session(root_ctx, user_ctx, api_key=api_key, member_id=member_id, account_name=account_name)


class _FakeOneBoundClient:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.calls: list[tuple[str, object]] = []
        self.preview_goods_fen = 12500
        self.preview_shipping_fen = 1000
        self.create_error: Exception | None = None
        self.order_status = "waitbuyerpay"
        self.order_status_label = "等待买家付款"
        self.agreement_ready = True
        self.pay_result = ("succeeded", "支付宝支付确认成功")
        self.created_orders: dict[str, dict] = {}
        self._seq = 1000

    def get_product(self, offer_id: str, source_url: str) -> dict:
        self.calls.append(("get_product", (offer_id, source_url)))
        return {
            "product": {
                "title": "日常办公收纳箱",
                "images": ["http://cbu01.alicdn.com/main.jpg"],
                "api_raw": {"companyName": "义乌日用百货厂"},
                "skus": [
                    {
                        "id": "5001",
                        "offer_id": offer_id,
                        "spec_id": "spec-white-m",
                        "options": {"颜色": "白色", "尺寸": "中号"},
                        "price": "25.00",
                        "stock": "120",
                        "image": "//cbu01.alicdn.com/sku1.jpg",
                    },
                    {
                        "id": "5002",
                        "offer_id": offer_id,
                        "spec_id": "spec-black-l",
                        "options": {"颜色": "黑色", "尺寸": "大号"},
                        "price": "30.00",
                        "stock": "80",
                        "image": "https://img.alicdn.com/sku2.jpg",
                    },
                    {
                        "id": "5003",
                        "offer_id": offer_id,
                        "spec_id": "",
                        "options": {"颜色": "缺标款"},
                        "price": "19.00",
                        "stock": "5",
                        "image": "",
                    },
                ],
            },
            "http_status": 200,
            "request": {"method": "product.search.queryProductDetail", "offer_id": offer_id},
        }

    def list_addresses(self):
        self.calls.append(("list_addresses", None))
        fields = {
            "fullName": "张三",
            "mobile": "13800138000",
            "phone": "",
            "provinceText": "广东省",
            "cityText": "深圳市",
            "areaText": "南山区",
            "townText": "粤海街道",
            "address": "科技园南路88号",
            "postCode": "518000",
        }
        candidate = {
            "id": "1688:addr-default",
            "kind": "1688",
            "label": "1688 保存地址",
            "text": "张三\n13800138000\n广东省 深圳市 南山区 粤海街道 科技园南路88号",
            "is_default": True,
            "blocked_reason": "",
        }
        return [(candidate, fields)]

    def preview_order(self, address: dict, cargo: list[dict]):
        self.calls.append(("preview_order", (address, cargo)))
        spec_to_sku = {"spec-white-m": "5001", "spec-black-l": "5002"}
        items = tuple(
            PreviewItem(
                offer_id=str(item["offer_id"]),
                sku_id=spec_to_sku[item["spec_id"]],
                spec_id=item["spec_id"],
                quantity=item["quantity"],
                quantity_reported=True,
            )
            for item in cargo
        )
        total = self.preview_goods_fen + self.preview_shipping_fen
        return [
            PreviewGroup(
                items=items,
                goods_fen=self.preview_goods_fen,
                shipping_fen=self.preview_shipping_fen,
                total_fen=total,
                flow="general",
                pay_channels=("alipay", "shegou"),
            )
        ]

    def create_order(self, address: dict, cargo: list[dict], *, flow: str, channel: str, external_id: str):
        self.calls.append(("create_order", (address, cargo, flow, channel, external_id)))
        if self.create_error is not None:
            raise self.create_error
        self._seq += 1
        number = f"8800{self._seq}"
        spec_to_sku = {"spec-white-m": "5001", "spec-black-l": "5002"}
        self.created_orders[external_id] = {
            "number": number,
            "products": [
                {
                    "offer_id": str(c["offer_id"]),
                    "sku_id": spec_to_sku[c["spec_id"]],
                    "spec_id": c["spec_id"],
                    "quantity": c["quantity"],
                }
                for c in cargo
            ],
            "total_fen": Decimal(self.preview_goods_fen + self.preview_shipping_fen),
        }
        return [number]

    def find_order_numbers(self, external_id: str):
        self.calls.append(("find_order_numbers", external_id))
        saved = self.created_orders.get(external_id)
        return [saved["number"]] if saved else []

    def get_order(self, number: str):
        self.calls.append(("get_order", number))
        saved = next(v for v in self.created_orders.values() if v["number"] == number)
        return BuyerOrder(
            status={"order_number": number, "status": self.order_status, "status_label": self.order_status_label},
            products=saved["products"],
            total_fen=saved["total_fen"],
            carrier_names={},
            products_complete=True,
        )

    def get_cashier_url(self, numbers: list[str]):
        self.calls.append(("get_cashier_url", numbers))
        return f"https://cashier.1688.com/pay?order={numbers[0]}"

    def get_payment_agreement(self, channel: str):
        self.calls.append(("get_payment_agreement", channel))
        return {
            "channel": channel,
            "ready": self.agreement_ready,
            "signed": self.agreement_ready,
            "bound": self.agreement_ready,
            "sign_url": "" if self.agreement_ready else "https://trade.1688.com/sign",
            "message": "支付宝免密支付已开通" if self.agreement_ready else "请先签约",
        }

    def pay_order(self, number: str, *, channel: str, amount_fen: int, request_id: str):
        self.calls.append(("pay_order", (number, channel, amount_fen, request_id)))
        return self.pay_result


def test_direct_purchase_forces_onebound_multi_sku_both_address_modes_and_stays_isolated(tmp_path: Path):
    root_ctx = _make_context(tmp_path / "root")
    try:
        # 管理员 ERP 主业务设为企业自用 alibaba，但万邦直购仍必须强制走 onebound。
        _setup_onebound_app_and_session(root_ctx, root_ctx, active_admin_provider="alibaba")
        assert AlibabaAuthorizationService(root_ctx.config).business_provider() == "alibaba"

        captured_configs: list[dict] = []
        fake_client = _FakeOneBoundClient({})

        def client_factory(config: dict):
            captured_configs.append(config)
            fake_client.config = config
            return fake_client

        service = alibaba_direct_purchase_facade.create_service(root_ctx, client_factory=client_factory)

        # 1. 初始状态就绪，记录为空
        initial = service.state()
        assert initial["configured"] is True
        assert initial["authorized"] is True
        assert initial["session_ready"] is True
        assert initial["blocked_reason"] == ""
        assert initial["records"] == []

        # 2. 解析含分享文案的 1688 链接，返回全部 SKU（含缺 specId 不可购项）
        resolved = service.resolve({"url": "【办公好物】https://detail.1688.com/offer/7788990011.html 快来抢"})
        assert resolved["offer_id"] == "7788990011"
        assert resolved["supplier"] == "义乌日用百货厂"
        assert resolved["main_image"] == "https://cbu01.alicdn.com/main.jpg"
        assert resolved["skus"][0]["image"] == "https://cbu01.alicdn.com/sku1.jpg"
        assert len(resolved["skus"]) == 3
        assert [s["purchasable"] for s in resolved["skus"]] == [True, True, False]
        assert captured_configs[-1]["application_type"] == "onebound"

        # 3. 选取 1688 保存地址 + 复选 2 个 SKU 预览下单
        addr_res = service.addresses()
        assert len(addr_res["items"]) == 1
        assert addr_res["items"][0]["id"] == "1688:addr-default"

        preview = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [
                    {"sku_id": "5001", "quantity": 2},
                    {"sku_id": "5002", "quantity": 3},
                ],
                "address_id": "1688:addr-default",
            }
        )["record"]
        assert preview["state"] == "preview"
        assert preview["preview"]["total_quantity"] == 5
        assert len(preview["preview"]["items"]) == 2
        assert preview["preview"]["total_fen"] == 13500

        # 创建订单（不自动免密扣款）
        created = service.create({"preview_id": preview["id"], "pay_channel": "alipay"})["record"]
        assert created["state"] == "created"
        assert created["payment"] is None
        assert len(created["order_numbers"]) == 1
        order_no = created["order_numbers"][0]

        # 打开收银台链接
        cashier = service.cashier({"preview_id": created["id"]})
        assert cashier["url"] == f"https://cashier.1688.com/pay?order={order_no}"

        # 买家通过收银台/App 付款后，刷新状态自动按 135 元 × 2 = 270 计入万邦额度（幂等）
        fake_client.order_status = "waitsellersend"
        fake_client.order_status_label = "等待卖家发货"
        reconciled = service.reconcile({"preview_id": created["id"]})["record"]
        assert reconciled["order_status_code"] == "waitsellersend"
        assert reconciled["quota_credited"] is True

        fp = key_fingerprint("ob-key-direct")
        summary_once = root_ctx.onebound_quota.read_buyer_summary(fp, "b2b-direct-1")
        assert summary_once["earned"] == 270
        assert summary_once["balance"] == 470  # 200 基础 + 270 订单收入

        # 重复刷新不重复入账
        service.reconcile({"preview_id": created["id"]})
        summary_twice = root_ctx.onebound_quota.read_buyer_summary(fp, "b2b-direct-1")
        assert summary_twice["earned"] == 270

        # 4. 手动输入地址（先解析 + 核对确认）+ 免密支付
        parsed_addr = service.parse_address({"text": "李四 13900139000 浙江省杭州市西湖区文三路100号"})
        assert parsed_addr["address"]["fullName"] == "李四"
        assert parsed_addr["address"]["mobile"] == "13900139000"

        # 未勾选确认拒绝预览
        with pytest.raises(ValueError, match="核对并确认"):
            service.preview(
                {
                    "resolve_token": resolved["resolve_token"],
                    "items": [{"sku_id": "5001", "quantity": 1}],
                    "address": {
                        "fullName": "李四",
                        "mobile": "13900139000",
                        "phone": "",
                        "provinceText": "浙江省",
                        "cityText": "杭州市",
                        "areaText": "西湖区",
                        "townText": "",
                        "address": "文三路100号",
                        "postCode": "",
                    },
                    "address_confirmed": False,
                }
            )

        fake_client.preview_goods_fen = 5000
        fake_client.preview_shipping_fen = 0
        fake_client.order_status = "waitbuyerpay"
        fake_client.order_status_label = "等待买家付款"
        preview2 = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 2}],
                "address": {
                    "fullName": "李四",
                    "mobile": "13900139000",
                    "phone": "",
                    "provinceText": "浙江省",
                    "cityText": "杭州市",
                    "areaText": "西湖区",
                    "townText": "",
                    "address": "文三路100号",
                    "postCode": "",
                },
                "address_confirmed": True,
            }
        )["record"]
        created2 = service.create({"preview_id": preview2["id"], "pay_channel": "alipay"})["record"]
        paid2 = service.pay({"preview_id": created2["id"], "confirmed_total_fen": 5000})["record"]
        assert paid2["payment"]["state"] == "succeeded"
        assert paid2["quota_credited"] is True
        summary_after_pay = root_ctx.onebound_quota.read_buyer_summary(fp, "b2b-direct-1")
        assert summary_after_pay["earned"] == 370  # +50元×2 = +100

        # 5. 彻底隔离验证：未写入 ERP 商品库、草稿库、或销售采购库
        assert root_ctx.products.load_products_index() == []
        assert root_ctx.products.load_drafts_index() == []
        assert not (root_ctx.paths.data_dir / "alibaba-self-purchases.sqlite3").exists()
        assert (root_ctx.paths.data_dir / "alibaba-direct-purchases.sqlite3").exists()
        assert len(service.state()["records"]) == 2
    finally:
        root_ctx.close()


def test_direct_purchase_price_drift_and_unknown_recovery(tmp_path: Path):
    root_ctx = _make_context(tmp_path / "root")
    try:
        _setup_onebound_app_and_session(root_ctx, root_ctx)
        fake_client = _FakeOneBoundClient({})
        service = alibaba_direct_purchase_facade.create_service(root_ctx, client_factory=lambda _c: fake_client)

        resolved = service.resolve({"url": "https://detail.1688.com/offer/7788990011.html"})
        preview = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 1}],
                "address_id": "1688:addr-default",
            }
        )["record"]

        # 超过 60 秒后下单前重新预览发现价格变化，拦截建单且不调 create_order
        with root_ctx.alibaba_direct_purchases._lock:
            root_ctx.alibaba_direct_purchases._previews[preview["id"]]["payload"]["previewed_at"] -= 120
        fake_client.preview_goods_fen = 15000
        failed = service.create({"preview_id": preview["id"], "pay_channel": "alipay"})["record"]
        assert failed["state"] == "failed"
        assert "已变化" in failed["message"]
        assert not any(call[0] == "create_order" for call in fake_client.calls)

        # 网络超时导致回执未知，后续 reconcile 通过 external_id 找回订单号
        preview2 = service.preview(
            {
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 1}],
                "address_id": "1688:addr-default",
            }
        )["record"]
        fake_client.create_error = AlibabaApiError("1688 网络请求失败，请稍后手动重试")
        unknown = service.create({"preview_id": preview2["id"], "pay_channel": "alipay"})["record"]
        assert unknown["state"] == "unknown"
        assert "勿重复下单" in unknown["message"]

        # 模拟 1688 实际已创建成功，reconcile 找回
        fake_client.created_orders[preview2["id"]] = {
            "number": "88009999",
            "products": [{"offer_id": "7788990011", "sku_id": "5001", "spec_id": "spec-white-m", "quantity": 1}],
            "total_fen": Decimal(16000),
        }
        recovered = service.reconcile({"preview_id": preview2["id"]})["record"]
        assert recovered["state"] == "created"
        assert recovered["order_numbers"] == ["88009999"]
    finally:
        root_ctx.close()


def test_direct_purchase_http_e2e_for_admin_and_normal_user(accounts_http):
    import urllib.error
    import urllib.request
    from tests.test_accounts import register

    origin, runtime, login = accounts_http
    root_ctx = runtime.root
    user = register(origin, "direct-buyer@example.com")
    admin_client = login()
    user_client = login(user["email"])
    user_ctx = runtime.context_for(runtime.store.by_email(user["email"]))

    _setup_onebound_app_and_session(root_ctx, root_ctx, member_id="b2b-admin", account_name="管理员买家")
    _bind_onebound_session(root_ctx, user_ctx, member_id="b2b-user", account_name="普通买家号")

    # 未登录请求被 401 拦截
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(origin + "/api/alibaba/direct-purchase/state")
    assert exc.value.code == 401

    fake_client = _FakeOneBoundClient({})
    with patch("erp_web.services.alibaba_direct_purchase_service.alibaba_provider", side_effect=lambda _c: fake_client):
        # 普通用户可用全套直购接口
        u_state = user_client.get(origin + "/api/alibaba/direct-purchase/state").json()
        assert u_state["session_ready"] is True
        assert u_state["member_id"] == "b2b-user"

        resolved = user_client.post(
            origin + "/api/alibaba/direct-purchase/resolve",
            json={"url": "https://detail.1688.com/offer/7788990011.html"},
        ).json()
        preview = user_client.post(
            origin + "/api/alibaba/direct-purchase/preview",
            json={
                "resolve_token": resolved["resolve_token"],
                "items": [{"sku_id": "5001", "quantity": 2}, {"sku_id": "5002", "quantity": 1}],
                "address_id": "1688:addr-default",
            },
        ).json()["record"]
        created = user_client.post(
            origin + "/api/alibaba/direct-purchase/create",
            json={"preview_id": preview["id"], "pay_channel": "alipay"},
        ).json()["record"]
        assert created["state"] == "created"
        assert len(created["order_numbers"]) == 1

        # 普通用户记录与管理员记录完全隔离
        assert len(user_client.get(origin + "/api/alibaba/direct-purchase/state").json()["records"]) == 1
        assert admin_client.get(origin + "/api/alibaba/direct-purchase/state").json()["records"] == []
        # 管理员同样可用直购接口
        a_addrs = admin_client.get(origin + "/api/alibaba/direct-purchase/addresses").json()
        assert len(a_addrs["items"]) == 1

"""1688 万邦个人直购服务：强制走万邦通道、多 SKU 下单与独立记录，不触碰 ERP 业务流。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import logging
import time
from urllib.parse import urlsplit

from erp_web.product_model.alibaba_purchase_model import alibaba_offer_id
from erp_web.schemas.alibaba_direct_purchase import (
    DirectPurchaseCreateRequest,
    DirectPurchaseParseAddressRequest,
    DirectPurchasePayRequest,
    DirectPurchasePaymentStatusRequest,
    DirectPurchasePreviewRequest,
    DirectPurchaseRecordRequest,
    DirectPurchaseResolveRequest,
)
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.services.alibaba_api_client import AlibabaApiError, AlibabaApiRejected, alibaba_provider, onebound_trade_ready
from erp_web.services.alibaba_product_payload import find_first_key, first_text
from erp_web.services.alibaba_purchase_address import address_text, parse_address
from erp_web.services.alibaba_purchase_payment import check_payment_amount, payment_label
from erp_web.services.alibaba_self_purchase_service import account_scope
from erp_web.services.collection_link_resolution import (
    _input_links,
    _resolve_short_link,
    _short_link_url,
    _target_offer_id,
)

logger = logging.getLogger(__name__)
PREVIEW_RECHECK_AFTER_SECONDS = 60.0
_PAID_ORDER_STATUSES = frozenset(
    {"waitsellersend", "waitbuyerreceive", "waitbuyerreceives", "confirm_goods", "success"}
)


def _resolve_1688_url_and_offer_id(raw_text: str) -> tuple[str, str]:
    """支持标准 1688 商品链接、分享文案及 qr.1688.com 短链。"""
    urls = _input_links(raw_text, "api")
    candidates: list[str] = []
    for url in urls:
        offer = alibaba_offer_id(url) or _target_offer_id(url)
        if offer:
            candidates.append(offer)
            continue
        parsed = urlsplit(url)
        if parsed.hostname == "qr.1688.com":
            resolved = _resolve_short_link(_short_link_url(url), time.monotonic() + 20)
            offer = alibaba_offer_id(resolved)
            if offer:
                candidates.append(offer)
    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise ValueError("未能识别有效的 1688 商品链接，请粘贴完整商品链接或 1688 分享文案")
    if len(unique) > 1:
        raise ValueError("检测到多个不同的 1688 商品链接，请每次仅粘贴一个商品链接")
    offer_id = unique[0]
    return f"https://detail.1688.com/offer/{offer_id}.html", offer_id


def _format_sku_specification(raw_sku: dict, fallback_title: str) -> str:
    options = raw_sku.get("options") if isinstance(raw_sku.get("options"), dict) else {}
    spec = " / ".join(
        f"{k}：{v}" for k, v in options.items() if str(k).strip() and str(v).strip()
    )
    return spec or str(raw_sku.get("name") or "").strip() or fallback_title or "默认规格"


def _normalize_image_url(raw: object) -> str:
    url = str(raw or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    if parsed.scheme == "http" and parsed.hostname.endswith(".alicdn.com"):
        parsed = parsed._replace(scheme="https")
    return parsed.geturl()


def _extract_supplier(raw_product: dict) -> str:
    api_raw = raw_product.get("api_raw")
    if isinstance(api_raw, dict):
        supplier = first_text(
            find_first_key(
                api_raw,
                {"sellerName", "companyName", "shopName", "supplierName", "sellerLoginId"},
            )
        )
        if supplier:
            return supplier[:200]
    return ""


def public_record(row: dict) -> dict:
    result = row["result"]
    payment = row.get("payment") or None
    return {
        "id": row["id"],
        "state": row["state"],
        "preview": row["payload"]["view"],
        "payment": payment if isinstance(payment, dict) and payment else None,
        "order_numbers": list(result.get("order_numbers") or []),
        "order_status": str(result.get("order_status") or ""),
        "order_status_code": str(result.get("order_status_code") or ""),
        "message": str((payment or {}).get("message") or result.get("message") or ""),
        "pay_channel": str(result.get("pay_channel") or ""),
        "quota_credited": bool(result.get("quota_credited")),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _build_cargo(items: list[dict]) -> list[dict]:
    seen_specs: set[tuple[str, str]] = set()
    cargo: list[dict] = []
    for item in items:
        key = (item["offer_id"], item["spec_id"])
        if key in seen_specs:
            raise ValueError("所选规格存在相同的下单标识（specId），请核对后再试")
        seen_specs.add(key)
        cargo.append(
            {
                "offer_id": item["offer_id"],
                "spec_id": item["spec_id"],
                "quantity": item["quantity"],
            }
        )
    return cargo


def _normalized_preview_view(previews, product: dict, items: list[dict], address: dict) -> dict:
    if len(previews) != 1:
        raise AlibabaApiError(
            "所选规格未返回单笔有效预览（可能被拆单或受起订量/库存限制），请调整勾选后重试"
        )
    preview = previews[0]
    expected = {
        (item["offer_id"], item["sku_id"], item["spec_id"]): item for item in items
    }
    seen: set[tuple[str, str, str]] = set()
    for returned in preview.items:
        key = (returned.offer_id, returned.sku_id, returned.spec_id)
        if key not in expected or key in seen:
            raise AlibabaApiError("1688 预览返回的商品、SKU 或规格与勾选内容不一致，已停止下单")
        target = expected[key]
        if returned.quantity_reported and returned.quantity != target["quantity"]:
            raise AlibabaApiError("1688 预览返回的商品数量与填写不一致，已停止下单")
        seen.add(key)
    if seen != set(expected):
        raise AlibabaApiError("1688 预览缺少部分已勾选规格，已停止下单")
    return {
        "offer_id": product["offer_id"],
        "product_url": product["product_url"],
        "title": product["title"],
        "supplier": product["supplier"],
        "main_image": product["main_image"],
        "items": [
            {
                "offer_id": item["offer_id"],
                "sku_id": item["sku_id"],
                "spec_id": item["spec_id"],
                "specification": item["specification"],
                "price": item["price"],
                "image": item["image"],
                "quantity": item["quantity"],
            }
            for item in items
        ],
        "total_quantity": sum(item["quantity"] for item in items),
        "recipient": address["fullName"],
        "phone": address["mobile"] or address["phone"],
        "address": address_text(address),
        "goods_fen": preview.goods_fen,
        "shipping_fen": preview.shipping_fen,
        "total_fen": preview.total_fen,
        "flow": preview.flow,
        "pay_channels": sorted(set(preview.pay_channels)),
        "expires_at": time.time() + 600,
    }


def _verify_direct_order(order, items: list[dict]) -> None:
    if not order.products_complete:
        raise AlibabaApiError("返回订单缺少商品明细，无法核验直购订单")
    expected: dict[tuple[str, str, str], int] = defaultdict(int)
    actual: dict[tuple[str, str, str], int] = defaultdict(int)
    for item in items:
        expected[(item["offer_id"], item["sku_id"], item["spec_id"])] += int(item["quantity"])
    for product in order.products:
        key = (product["offer_id"], product["sku_id"], product["spec_id"])
        count = product["quantity"]
        if not count:
            raise AlibabaApiError("返回订单缺少商品数量，无法核验直购订单")
        actual[key] += int(count)
    if actual != expected:
        raise AlibabaApiError("返回订单的商品、规格或数量与本次直购记录不一致")


class AlibabaDirectPurchaseService:
    """个人 1688 万邦直购编排服务；强制通过万邦企业 Key + 个人已授权买家执行。"""

    def __init__(
        self,
        store,
        *,
        config_provider,
        status_provider,
        client_factory=None,
        read_cache=None,
        quota_credit=None,
    ) -> None:
        self.store = store
        self.config_provider = config_provider
        self.status_provider = status_provider
        self.client_factory = client_factory or alibaba_provider
        self.read_cache = read_cache
        self.quota_credit = quota_credit

    def _onebound_config(self, *, require_session: bool = False) -> dict:
        config = self.config_provider()
        if config.get("application_type") != "onebound":
            raise ValueError("1688 直购仅支持通过万邦企业 Key 执行")
        if require_session and not onebound_trade_ready(config):
            raise ValueError("万邦直购下单与支付需要已核验的买家授权，请到「平台授权 → 万邦 1688」重新完成自助授权")
        return config

    def _credit_quota(self, config: dict, order_number: str, total_fen) -> bool:
        if not callable(self.quota_credit) or not order_number or total_fen is None:
            return False
        try:
            self.quota_credit(config, order_number, total_fen)
            return True
        except Exception:
            logger.exception("万邦直购付款额度入账失败")
            return False

    def state(self) -> dict:
        status = self.status_provider()
        scope = str(status.get("account_scope") or "")
        records = [public_record(row) for row in self.store.list_records(scope)] if scope else []
        return {
            "ok": True,
            "configured": bool(status["configured"]),
            "authorized": bool(status["authorized"]),
            "session_ready": bool(status["session_ready"]),
            "member_id": str(status["member_id"] or ""),
            "account_name": str(status["account_name"] or ""),
            "blocked_reason": str(status["blocked_reason"] or ""),
            "records": records,
        }

    def _load_saved_addresses(self, config: dict, *, refresh: bool = False):
        scope = account_scope(config)
        if self.read_cache is not None and not refresh:
            cached = self.read_cache.get_addresses(scope)
            if cached is not None:
                return cached
        saved = self.client_factory(config).list_addresses()
        if scope != account_scope(self._onebound_config()):
            raise ValueError("万邦授权账号已变化，请刷新地址列表")
        if self.read_cache is not None:
            self.read_cache.set_addresses(scope, saved)
        return saved

    def _get_agreement(self, config: dict, channel: str, *, refresh: bool = False) -> dict:
        scope = account_scope(config)
        if self.read_cache is not None and not refresh:
            cached = self.read_cache.get_payment_agreement(scope, channel)
            if cached is not None:
                return cached
        agreement = self.client_factory(config).get_payment_agreement(channel)
        if scope != account_scope(self._onebound_config(require_session=True)):
            raise ValueError("万邦授权已切换，请重新查询免密签约状态")
        if self.read_cache is not None:
            self.read_cache.set_payment_agreement(scope, channel, agreement)
        return agreement

    def addresses(self, *, refresh: bool = False) -> dict:
        config = self._onebound_config()
        notice = ""
        items = []
        try:
            saved = self._load_saved_addresses(config, refresh=refresh)
            items = [candidate for candidate, _ in saved]
        except (AlibabaApiError, ExternalRequestBlocked) as exc:
            notice = f"1688 地址读取失败（{exc}），可重试或切换手动输入地址"
        return {"ok": True, "items": items, "notice": notice}

    def parse_address(self, body: dict) -> dict:
        req = DirectPurchaseParseAddressRequest.model_validate(body)
        return parse_address(req.text)

    def resolve(self, body: dict) -> dict:
        req = DirectPurchaseResolveRequest.model_validate(body)
        product_url, offer_id = _resolve_1688_url_and_offer_id(req.url)
        config = self._onebound_config()
        scope = account_scope(config)
        provider_type = str(config.get("application_type") or "onebound")
        payload = None
        if self.read_cache is not None and not req.refresh:
            payload = self.read_cache.get_product(provider_type, scope, offer_id)
        if payload is None:
            payload = self.client_factory(config).get_product(offer_id, product_url)
            if self.read_cache is not None and isinstance(payload, dict):
                self.read_cache.set_product(provider_type, scope, offer_id, payload)
        product = payload.get("product") if isinstance(payload, dict) else {}
        if not isinstance(product, dict):
            raise ValueError("1688 未返回有效商品信息，请稍后重试")
        title = str(product.get("title") or "").strip() or f"1688 商品 {offer_id}"
        supplier = _extract_supplier(product)
        images = product.get("images") if isinstance(product.get("images"), list) else []
        main_image = next((norm for raw in images if (norm := _normalize_image_url(raw))), "")
        raw_skus = product.get("skus") if isinstance(product.get("skus"), list) else []
        skus: list[dict] = []
        sku_map: dict[str, dict] = {}
        seen: set[str] = set()
        for row in raw_skus:
            if not isinstance(row, dict):
                continue
            sku_id = str(row.get("id") or "").strip()
            if not sku_id or sku_id in seen:
                continue
            seen.add(sku_id)
            row_offer = str(row.get("offer_id") or offer_id).strip() or offer_id
            spec_id = str(row.get("spec_id") or "").strip()
            specification = _format_sku_specification(row, title)
            block_reason = ""
            if not sku_id.isascii() or not sku_id.isdigit():
                block_reason = "该规格缺少有效的数字 1688 SKU 编号，暂不支持直购"
            elif row_offer != offer_id:
                block_reason = "该规格返回的商品编号与链接不一致，暂不支持直购"
            elif not spec_id:
                block_reason = "1688 未返回该规格的下单标识（specId），暂不支持直购"
            sku_image = _normalize_image_url(row.get("image"))
            if not main_image and sku_image:
                main_image = sku_image
            entry = {
                "sku_id": sku_id,
                "spec_id": spec_id,
                "specification": specification,
                "price": str(row.get("price") or "").strip(),
                "stock": str(row.get("stock") or "").strip(),
                "image": sku_image or main_image,
                "purchasable": not block_reason,
                "block_reason": block_reason,
            }
            skus.append(entry)
            sku_map[sku_id] = {**entry, "offer_id": offer_id}
        if main_image:
            for entry in skus:
                if not entry["image"]:
                    entry["image"] = main_image
                    sku_map[entry["sku_id"]]["image"] = main_image
        product_meta = {
            "offer_id": offer_id,
            "product_url": product_url,
            "title": title,
            "supplier": supplier,
            "main_image": main_image,
        }
        token = self.store.save_resolved(scope, product_meta, sku_map)
        return {
            "ok": True,
            "resolve_token": token,
            **product_meta,
            "skus": skus,
        }

    def preview(self, body: dict) -> dict:
        req = DirectPurchasePreviewRequest.model_validate(body)
        config = self._onebound_config(require_session=True)
        scope = account_scope(config)
        resolved = self.store.get_resolved(req.resolve_token, scope)
        sku_map = resolved["sku_map"]
        items: list[dict] = []
        for selection in req.items:
            sku = sku_map.get(selection.sku_id)
            if sku is None:
                raise ValueError("所选规格已失效，请重新解析商品链接")
            if not sku["purchasable"]:
                raise ValueError(sku["block_reason"] or "所选规格暂不支持直购")
            items.append({**sku, "quantity": selection.quantity})
        client = self.client_factory(config)
        if req.address is not None:
            address = req.address.model_dump()
        else:
            saved = self._load_saved_addresses(config, refresh=False)
            matches = [
                (candidate, fields)
                for candidate, fields in saved
                if candidate["id"] == req.address_id
            ]
            if len(matches) != 1 and self.read_cache is not None:
                saved = self._load_saved_addresses(config, refresh=True)
                matches = [
                    (candidate, fields)
                    for candidate, fields in saved
                    if candidate["id"] == req.address_id
                ]
            if len(matches) != 1:
                raise ValueError("收货地址已变化，请刷新 1688 地址列表并重新选择")
            selected_candidate, address = matches[0]
            if selected_candidate["blocked_reason"]:
                raise ValueError(selected_candidate["blocked_reason"])
        cargo = _build_cargo(items)
        view = _normalized_preview_view(
            client.preview_order(address, cargo), resolved["product"], items, address
        )
        if scope != account_scope(self._onebound_config(require_session=True)):
            raise ValueError("查询期间万邦授权已变化，请重新预览")
        row = self.store.save_preview(
            scope,
            {
                "purchase": {"address": address, "cargo": cargo},
                "items": view["items"],
                "view": view,
                "previewed_at": time.time(),
            },
        )
        return {"ok": True, "record": public_record(row)}

    def create(self, body: dict) -> dict:
        req = DirectPurchaseCreateRequest.model_validate(body)
        config = self._onebound_config(require_session=True)
        scope = account_scope(config)
        self.store.get(req.preview_id, scope)
        client = self.client_factory(config)
        row, claimed = self.store.claim(req.preview_id, scope, req.pay_channel)
        if not claimed:
            return {"ok": True, "record": public_record(row)}
        result: dict = {"pay_channel": req.pay_channel}
        sent = False
        try:
            view = row["payload"]["view"]
            purchase = row["payload"]["purchase"]
            address, cargo = purchase["address"], purchase["cargo"]
            previewed_at = float(row["payload"].get("previewed_at") or 0.0)
            if (time.time() - previewed_at) > PREVIEW_RECHECK_AFTER_SECONDS:
                product_meta = {
                    "offer_id": view["offer_id"],
                    "product_url": view["product_url"],
                    "title": view["title"],
                    "supplier": view["supplier"],
                    "main_image": view["main_image"],
                }
                current = _normalized_preview_view(
                    client.preview_order(address, cargo), product_meta, row["payload"]["items"], address
                )
                if any(
                    current[k] != view[k]
                    for k in ("goods_fen", "shipping_fen", "total_fen", "flow", "pay_channels")
                ):
                    raise ValueError("商品价格、运费或可用支付渠道已变化，请重新预览后下单")
            if scope != account_scope(self._onebound_config(require_session=True)):
                raise ValueError("万邦授权账号已变化，请重新预览")
            sent = True
            numbers = client.create_order(
                address, cargo, flow=view["flow"], channel=req.pay_channel, external_id=row["id"]
            )
            result.update(
                order_numbers=numbers,
                order_status="等待买家付款",
                order_status_code="waitbuyerpay",
                message="1688 订单已创建（未自动扣款）。可点击「打开 1688 收银台」付款、在手机 1688 App 付款，或使用已开通的免密支付。",
            )
            row = self.store.finish(row["id"], scope, "created", result)
        except Exception as exc:
            not_sent = isinstance(exc, AlibabaApiRejected) or (
                isinstance(exc, ExternalRequestBlocked)
                and exc.details.get("definitively_rejected")
            )
            unknown = sent and not not_sent
            message = (
                "创建结果待核验，请点击「刷新状态」核对原请求，勿重复下单"
                if unknown
                else str(exc)
                if isinstance(exc, (ValueError, AlibabaApiError))
                else "下单前检查失败，请核对万邦授权状态后重试"
            )
            result["message"] = message
            row = self.store.finish(row["id"], scope, "unknown" if unknown else "failed", result)
        return {"ok": True, "record": public_record(row)}

    def reconcile(self, body: dict) -> dict:
        req = DirectPurchaseRecordRequest.model_validate(body)
        config = self._onebound_config()
        scope = account_scope(config)
        row = self.store.get(req.preview_id, scope)
        if row["state"] not in {"created", "unknown", "submitting", "closed"}:
            raise ValueError("该直购记录尚未下单，无需查询订单")
        if (
            row["state"] == "submitting"
            and (datetime.now(timezone.utc) - datetime.fromisoformat(row["updated_at"])).total_seconds()
            < 120
        ):
            return {
                "ok": True,
                "record": {**public_record(row), "message": "原下单请求仍在提交窗口内，请稍后再查"},
            }
        client = self.client_factory(config)
        result = dict(row["result"])
        numbers = list(result.get("order_numbers") or [])
        if not numbers:
            numbers = client.find_order_numbers(row["id"])
            if len(numbers) != 1:
                return {
                    "ok": True,
                    "record": {
                        **public_record(row),
                        "message": "暂未查到唯一匹配回执；未查到不等于创建失败，请到 1688 核对，勿重复下单",
                    },
                }
        order = client.get_order(numbers[0])
        _verify_direct_order(order, row["payload"]["items"])
        if scope != account_scope(self._onebound_config()):
            raise ValueError("万邦授权已切换，请重新查询")
        status_code = str(order.status.get("status") or "")
        status_label = str(order.status.get("status_label") or status_code)
        result.update(
            order_numbers=numbers,
            order_status=status_label,
            order_status_code=status_code,
            message="已从 1688 同步最新订单状态；付款与物流以 1688 为准",
        )
        state = "closed" if status_code in {"cancel", "terminated", "success"} else "created"
        if status_code in _PAID_ORDER_STATUSES:
            if self._credit_quota(config, numbers[0], order.total_fen):
                result["quota_credited"] = True
        row = self.store.finish(row["id"], scope, state, result)
        payment = row.get("payment") or {}
        if payment.get("state") in {"submitting", "unknown"} and status_code in _PAID_ORDER_STATUSES:
            row = self.store.finish_payment(
                row["id"],
                scope,
                payment["request_id"],
                "settled",
                "订单已进入发货或收货阶段，停止再次支付；实际支付渠道以 1688 为准",
            )
        return {"ok": True, "record": public_record(row)}

    def cashier(self, body: dict) -> dict:
        req = DirectPurchaseRecordRequest.model_validate(body)
        config = self._onebound_config()
        scope = account_scope(config)
        row = self.store.get(req.preview_id, scope)
        numbers = list(row["result"].get("order_numbers") or [])
        if row["state"] != "created" or len(numbers) != 1:
            raise ValueError("请先成功创建订单后再打开 1688 收银台")
        return {"ok": True, "url": self.client_factory(config).get_cashier_url(numbers)}

    def payment_status(self, body: dict) -> dict:
        req = DirectPurchasePaymentStatusRequest.model_validate(body)
        config = self._onebound_config(require_session=True)
        agreement = self._get_agreement(config, req.pay_channel, refresh=req.refresh)
        return {"ok": True, "agreement": agreement}

    def pay(self, body: dict) -> dict:
        req = DirectPurchasePayRequest.model_validate(body)
        config = self._onebound_config(require_session=True)
        scope = account_scope(config)
        row = self.store.get(req.preview_id, scope)
        if row.get("payment"):
            return {"ok": True, "record": public_record(row)}
        channel = row["result"].get("pay_channel")
        numbers = list(row["result"].get("order_numbers") or [])
        if row["state"] != "created" or channel not in {"alipay", "shegou"} or len(numbers) != 1:
            raise ValueError("请先成功创建已选择支付渠道的直购订单")
        if req.confirmed_total_fen != row["payload"]["view"]["total_fen"]:
            raise ValueError("确认金额与直购预览不同，请重新核对")
        client = self.client_factory(config)
        agreement = self._get_agreement(config, channel, refresh=False)
        if not agreement["ready"]:
            raise ValueError(agreement["message"])
        number = numbers[0]
        order = client.get_order(number)
        if order.status["status"] != "waitbuyerpay":
            raise ValueError("1688 订单已不是待付款状态，请刷新订单状态，不会再次支付")
        check_payment_amount(order.total_fen, req.confirmed_total_fen)
        _verify_direct_order(order, row["payload"]["items"])
        if scope != account_scope(self._onebound_config(require_session=True)):
            raise ValueError("万邦授权已切换，请重新核对支付")
        row, claimed = self.store.claim_payment(row["id"], scope, req.confirmed_total_fen, channel)
        if not claimed:
            return {"ok": True, "record": public_record(row)}
        payment = row["payment"]
        try:
            state, message = client.pay_order(
                number,
                channel=payment["channel"],
                amount_fen=payment["amount_fen"],
                request_id=payment["request_id"],
            )
        except Exception as exc:
            rejected = isinstance(exc, AlibabaApiRejected) or (
                isinstance(exc, ExternalRequestBlocked)
                and exc.details.get("definitively_rejected")
            )
            if rejected and self.read_cache is not None:
                self.read_cache.invalidate_payment_agreement(scope, channel)
            state = "rejected" if rejected else "unknown"
            message = (
                str(exc)
                if isinstance(exc, AlibabaApiRejected)
                else (
                    "1688 已拒绝本次支付，请检查授权配置后重试"
                    if rejected
                    else f"{payment_label(channel)}未取得确认回执，请刷新订单状态，勿重复支付"
                )
            )
        row = self.store.finish_payment(row["id"], scope, payment["request_id"], state, message)
        if state == "succeeded":
            if self._credit_quota(config, number, payment["amount_fen"]):
                row = self.store.mark_quota_credited(row["id"], scope)
        return {"ok": True, "record": public_record(row)}


__all__ = ["AlibabaDirectPurchaseService", "public_record"]

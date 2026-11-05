"""Yandex 费用批量读取：按本轮缺少的报价合并请求，严格核验逐项回执。"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import hashlib
import logging
from time import perf_counter
from typing import Callable

from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.schemas.commissions import CommissionRule
from erp_web.services.pricing_fee_rules import rule_fee

logger = logging.getLogger("erp.pricing")
ENDPOINT = "/v2/tariffs/calculate"


class CommissionBatchPending(Exception):
    """本地计算尚缺官方报价，由核价批次统一获取后继续。"""


def _number(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Yandex 官方佣金金额或规则无效") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Yandex 官方佣金金额或规则无效")
    return result


def parse_rule(tariff: dict, price: Decimal) -> CommissionRule | None:
    """仅采用字段完整、含义明确且能复现本次真实费用的规则。"""
    parameters = tariff.get("parameters")
    if not isinstance(parameters, list):
        return None
    values = {}
    known = {"value", "valueType", "billingUnit", "priceDependence", "priceFrom", "priceTo", "minValue", "maxValue"}
    for item in parameters:
        if not isinstance(item, dict) or item.get("name") not in known or item["name"] in values:
            return None
        values[item["name"]] = item.get("value")
    kind = values.get("valueType")
    if kind not in {"relative", "absolute"} or values.get("billingUnit") != "item":
        return None
    independent = values.get("priceDependence") == "NOT_DEPENDED"
    if not independent and not all(key in values for key in ("priceFrom", "priceTo")):
        return None
    try:
        rule: CommissionRule = {
            "kind": kind,
            "value": str(_number(values.get("value"))),
            "price_from": str(_number(values.get("priceFrom", 0))),
            "price_to": str(_number(values["priceTo"])) if "priceTo" in values else None,
            "minimum": str(_number(values["minValue"])) if "minValue" in values else None,
            "maximum": str(_number(values["maxValue"])) if "maxValue" in values else None,
        }
        if rule["price_to"] is not None and Decimal(rule["price_to"]) <= Decimal(rule["price_from"]):
            return None
        if rule["minimum"] is not None and rule["maximum"] is not None and Decimal(rule["minimum"]) > Decimal(rule["maximum"]):
            return None
        return rule if rule_fee(rule, price) == _number(tariff.get("amount")) else None
    except ValueError:
        return None


class YandexCommissionBatch:
    def __init__(self, store: dict, request: Callable, *, rule_cache=None):
        self.store = store
        self.request = request
        self.receipts: dict[str, dict | ValueError] = {}
        self.pending: dict[str, tuple[dict, str, str]] = {}
        self.failure = ""
        self.request_count = 0
        self.duration_ms = 0.0
        self.rule_cache = rule_cache
        # 缓存不保留凭据明文；账户、授权或包装参数变化不会借用原规则。
        self.account_key = hashlib.sha256(json.dumps(store, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def key(offer: dict, currency: str, campaign_id: str = '') -> str:
        return json.dumps([campaign_id, currency, offer], sort_keys=True)

    def rule_key(self, offer: dict, currency: str, campaign_id: str = '') -> str:
        return hashlib.sha256(self.key(
            {"account": self.account_key, **{k: v for k, v in offer.items() if k != "price"}}, currency, campaign_id or str(self.store.get('campaign_id') or '')
        ).encode()).hexdigest()

    def quote(self, offer: dict, currency: str, *, force_official=False, campaign_id: str = '') -> dict:
        if self.failure:
            raise ValueError(self.failure)
        campaign_id = campaign_id or str(self.store.get('campaign_id') or '')
        key = self.key(offer, currency, campaign_id)
        receipt = self.receipts.get(key)
        if isinstance(receipt, ValueError):
            raise receipt
        if receipt is not None:
            return deepcopy(receipt)
        if not force_official and self.rule_cache is not None:
            rule = self.rule_cache.get(self.rule_key(offer, currency, campaign_id))
            amount = rule_fee(rule, Decimal(str(offer["price"]))) if rule else None
            if amount is not None:
                return {"amount": str(amount), "currency": currency, "cached_rule": rule}
        self.pending[key] = (deepcopy(offer), currency, campaign_id)
        raise CommissionBatchPending

    def quote_one(self, offer: dict, currency: str, *, force_official=False, campaign_id: str = '') -> dict:
        """独立费用查询也使用同一个批量协议与回执校验。"""
        try:
            return self.quote(offer, currency, force_official=True, campaign_id=campaign_id)
        except CommissionBatchPending:
            self.flush()
        return self.quote(offer, currency, force_official=True, campaign_id=campaign_id)

    def fail_pending(self, message: str) -> None:
        self.failure = message
        for key in self.pending:
            self.receipts[key] = ValueError(message)
        self.pending.clear()

    def flush(self) -> None:
        groups = defaultdict(list)
        for key, (offer, currency, campaign_id) in self.pending.items():
            groups[(currency, campaign_id)].append((key, offer))
        self.pending.clear()
        for (currency, campaign_id), items in groups.items():
            for offset in range(0, len(items), 200):
                chunk = items[offset:offset + 200]
                if self.failure:
                    for key, _ in chunk:
                        self.receipts[key] = ValueError(self.failure)
                    continue
                payload = {"parameters": {"campaignId": int(campaign_id)}, "offers": [offer for _, offer in chunk]}
                started = perf_counter()
                self.request_count += 1
                try:
                    body = self.request("POST", ENDPOINT, self.store["api_token"], payload)
                    result = body.get("result") if isinstance(body, dict) else None
                    rows = result.get("offers") if isinstance(result, dict) else None
                    if (not isinstance(rows, list) or len(rows) != len(chunk)
                            or any(not isinstance(row, dict) or row.get("offer") != offer for (_, offer), row in zip(chunk, rows))):
                        raise ValueError("Yandex 官方佣金批量回执与本次商品参数或顺序不一致")
                    for (key, _offer), row in zip(chunk, rows, strict=True):
                        try:
                            tariffs = row.get("tariffs")
                            fees = [item for item in tariffs if isinstance(item, dict) and item.get("type") == "FEE"] if isinstance(tariffs, list) else []
                            if len(fees) != 1:
                                raise ValueError("Yandex 未返回唯一的销售佣金 FEE，无法确认当前店铺费率")
                            fee = fees[0]
                            normalize = lambda unit: "RUB" if unit == "RUR" else unit
                            if normalize(str(fee.get("currency") or "").upper()) != normalize(currency):
                                raise ValueError("Yandex 官方佣金币种与当前店铺发布币种不一致")
                            _number(fee.get("amount"))
                            self.receipts[key] = deepcopy(fee)
                            if self.rule_cache is not None:
                                rule_key = self.rule_key(_offer, currency, campaign_id)
                                rule = parse_rule(fee, Decimal(str(_offer["price"])))
                                if rule is None:
                                    self.rule_cache.discard(rule_key)
                                else:
                                    self.rule_cache.put(rule_key, rule)
                        except ValueError as exc:
                            self.receipts[key] = exc
                except PublishAdapterError as exc:
                    detail = str(exc)
                    for field in ("api_token", "api_key", "access_token", "refresh_token", "client_secret"):
                        secret = str(self.store.get(field) or "")
                        if secret:
                            detail = detail.replace(secret, "***")
                    message = f"yandex 官方佣金查询失败（{exc.code}）：{detail[:500]}"
                    if exc.retryable or any(part in exc.code for part in ("AUTH_FAILED", "RATE_LIMITED", "CREDENTIAL", "ACCOUNT_DISABLED")):
                        self.failure = message
                    for key, _ in chunk:
                        self.receipts[key] = ValueError(message)
                except ValueError as exc:
                    for key, _ in chunk:
                        self.receipts[key] = exc
                finally:
                    elapsed = (perf_counter() - started) * 1000
                    self.duration_ms += elapsed
                    logger.info("Yandex 费用批量查询：%d 项，耗时 %.2f 秒，本轮累计 %d 次请求", len(chunk), elapsed / 1000, self.request_count)


__all__ = ["CommissionBatchPending", "YandexCommissionBatch", "parse_rule"]

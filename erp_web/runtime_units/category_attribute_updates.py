"""属性写入前的确定性校验；商品事实判断由主对话完成。"""

from typing import Any, Callable

from erp_web.runtime_units.category_store import fetch_category_record, fetch_category_attribute_values
from erp_web.schemas.category import category_attribute_schema, category_attribute_value_is_valid, category_attribute_value_mode
from erp_web.runtime_units.category_attribute_access import attribute_write_scope
from erp_web.services.capability_errors import BusinessCapabilityError


def validate_attribute_updates(platform: str, site: str, category_id: str,
                               updates: dict[str, Any], *, sku_scope: bool,
                               timeout: Callable[[], float] = lambda: 30) -> list[dict[str, Any]]:
    """重新取得平台规则；拒绝跨作用域字段及不真实的枚举 ID/值组合。"""
    try:
        record = fetch_category_record(platform, category_id, site=site, include_attributes=True, timeout_seconds=timeout())
    except Exception as exc:
        raise BusinessCapabilityError("CATEGORY_LIVE_API_FAILED", str(exc), retryable=True) from exc
    if str(record.get("category_id") or "") != category_id:
        raise BusinessCapabilityError("CATEGORY_CHANGED", "平台返回的类目与本次写入不一致，请重新查询。")
    all_definitions = {attr["id"]: attr for attr in category_attribute_schema(record)}
    definitions = {key: attr for key, attr in all_definitions.items()
                   if attribute_write_scope(platform, attr) == ("sku" if sku_scope else "common")}
    candidates = []
    for attr_id, value in updates.items():
        if attribute_write_scope(platform, all_definitions.get(attr_id, {"id": attr_id})) == "managed":
            raise BusinessCapabilityError("ATTRIBUTE_MANAGED_BY_DRAFT", f"属性 {attr_id} 由草稿或 SKU 的包装、编码及刊登设置派生，不能作为类目属性写入；请读取对应资料，缺失时向用户说明。")
        definition = definitions.get(attr_id)
        if definition is None:
            raise BusinessCapabilityError("ATTRIBUTE_OUTSIDE_SCOPE", f"属性 {attr_id} 不属于当前类目的可写{'SKU 差异' if sku_scope else '公共'}属性。")
        if value is None:
            continue
        if not category_attribute_value_is_valid(definition, value):
            raise BusinessCapabilityError("ATTRIBUTE_VALUE_INVALID", f"属性 {attr_id} 不符合平台值类型、数量或单位要求，请查询定义后修正。")
        if isinstance(value, dict) and isinstance(value.get("values"), list):
            if not all(isinstance(item, dict) for item in value["values"]):
                raise BusinessCapabilityError("ATTRIBUTE_VALUE_INVALID", f"属性 {attr_id} 的 values 包含无效项。")
            for item in value["values"]:
                if category_attribute_value_mode(definition) == "strict_enum" or item.get("dictionary_value_id"):
                    candidates.append((attr_id, item))
    # 先检查整批字段作用域与格式，避免已知不可写字段引发无意义的枚举网络请求。
    for attr_id, item in candidates:
        _verify_candidate(platform, site, category_id, attr_id, item, timeout)
    return list(definitions.values())


def _verify_candidate(platform, site, category_id, attr_id, item, timeout):
    cursor = ""
    seen = set()
    value = str(item["value"])
    # Ozon 的搜索端点不接受单字符；短值通过官方字典分页按 ID 与原文核对。
    query = "" if platform == "ozon" and len(value.strip()) < 2 else value
    # 只核对调用方给出的目标值，不做关键词推断，也不把不完整查询当作可写依据。
    for _ in range(20):
        try:
            page = fetch_category_attribute_values(platform, category_id, attr_id, site=site,
                                                   query=query, cursor=cursor, limit=100, timeout_seconds=timeout())
        except ValueError as exc:
            raise BusinessCapabilityError("ATTRIBUTE_CANDIDATE_QUERY_INVALID", str(exc), retryable=False) from exc
        except Exception as exc:
            raise BusinessCapabilityError("CATEGORY_LIVE_API_FAILED", str(exc), retryable=True) from exc
        if any(str(candidate.get("id")) == str(item.get("dictionary_value_id"))
               and str(candidate.get("value")) == str(item["value"])
               for candidate in page.get("values", [])):
            return
        cursor = str(page.get("next_cursor") or "")
        if not page.get("has_more") or not cursor or cursor in seen:
            break
        seen.add(cursor)
    raise BusinessCapabilityError("ATTRIBUTE_CANDIDATE_UNVERIFIED", f"无法确认属性 {attr_id} 的枚举 ID 与值，请重新查询平台候选；本次未写入。")


__all__ = ["validate_attribute_updates"]

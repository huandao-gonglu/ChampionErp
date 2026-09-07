"""复用来源选项到平台明确允许自定义值的字段；翻译不负责选择类目或枚举。"""

from copy import deepcopy
from typing import Any, Callable

from erp_web.product_model.sku_model import record, editable_selected_skus, text
from erp_web.schemas.category import category_attribute_schema, category_attribute_value_is_valid


# 绑定平台公开字段身份，不把整条 SKU 名称或其他选项拼进颜色。
# 供应商在“颜色”选项内声明的系列/花色名称原样保留，尺寸使用自己的字段。
SOURCE_BINDINGS = {
    "ozon": {"10097": ("颜色", "颜色分类", "色号", "color"),
             "9533": ("尺码", "尺寸", "size")},
    "yandex": {"14871214": ("颜色", "颜色分类", "色号", "color")},
    "mercadolibre": {"COLOR": ("颜色", "颜色分类", "色号", "color"),
                     "SIZE": ("尺码", "尺寸", "size")},
}


def reuse_sku_source_attributes(
    product: dict[str, Any], platform: str, category_record: dict[str, Any], *,
    translator: Callable[..., dict[str, str]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """批量补空值并保存翻译证据，相同语言和来源文本跨 SKU/市场复用。"""
    updated = deepcopy(product)
    draft = updated["drafts"][platform]
    if text(category_record.get("category_id")) != text(draft.get("category_id")):
        raise ValueError("类目已变化，请重新加载当前类目的属性。")
    language = text(draft.get("language"))
    if not language:
        raise ValueError("请先设置当前目标市场的刊登语言。")
    key = f"{platform}:{draft.get('site', '')}".lower()
    definitions = {item["id"]: item for item in category_attribute_schema(category_record)
                   if item.get("variation_role") == "variant" and not item.get("read_only")
                   and item.get("value_mode") in {"free_text", "open_enum"}}
    cached: dict[str, str] = {}
    for row in draft.get("sku_items", []):
        cached.update(record(record(row.get("source_option_translations")).get(language)))
    pending: list[tuple[dict[str, Any], str, str]] = []
    for fact, row in editable_selected_skus(updated, draft):
        own = record(record(row.get("attributes_by_target")).get(key))
        options = {text(name).casefold(): text(value) for name, value in record(fact.get("options")).items()}
        for attr_id, names in SOURCE_BINDINGS.get(platform, {}).items():
            if attr_id not in definitions or text(own.get(attr_id, record(draft.get("attributes")).get(attr_id))):
                continue
            # 同义来源字段若冲突，不猜一个值。
            values = {options[name] for name in names if options.get(name)}
            if len(values) == 1:
                pending.append((row, attr_id, values.pop()))
    unique = list(dict.fromkeys(source for _, _, source in pending if not cached.get(source)))
    # 沿用通用翻译能力的请求边界，不新增 Agent loop 或翻译协议。
    for start in range(0, len(unique), 100):
        content = {str(index): value for index, value in enumerate(unique[start:start + 100])}
        translated = translator(language, content)
        cached.update({value: translated[index] for index, value in content.items()})
    filled = []
    for row, attr_id, source in pending:
        value = cached[source]
        if not category_attribute_value_is_valid(definitions[attr_id], value):
            continue
        row.setdefault("attributes_by_target", {}).setdefault(key, {})[attr_id] = value
        row.setdefault("source_option_translations", {}).setdefault(language, {})[source] = value
        filled.append(f"{row['sku_id']}:{attr_id}")
    return updated, {"source": "source_translation", "sku_sources": True,
                     "ai_filled": filled, "need_review": []}


__all__ = ["reuse_sku_source_attributes"]

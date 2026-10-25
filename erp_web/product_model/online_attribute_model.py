"""在线属性的纯数据规则；不查询类目或写入平台。"""
from erp_web.schemas.online_products import OnlineListing, OnlineYandexAttributeValue


def yandex_category_id(listing: OnlineListing) -> str:
    category = (listing.snapshot.get("mapping", {}).get("marketCategoryId")
                or listing.snapshot.get("offer", {}).get("marketCategoryId"))
    if listing.platform != "yandex" or not str(category or "").isdigit() or int(category) <= 0:
        raise ValueError("在线商品缺少有效的 Yandex 类目，请先同步商品")
    return str(category)


def validate_yandex_attribute_rows(rows):
    """先校验 wire 形状，平台类目及候选校验由领域服务执行。"""
    for row in rows:
        OnlineYandexAttributeValue.model_validate(row)
        if str(row["parameterId"]) != row["id"]:
            raise ValueError("属性身份无效，不能修改属性编号或传入额外字段")


def merge_yandex_attributes(existing, updates):
    """按属性替换整组值，保留未编辑字段和多值项。"""
    changed = {str(row["id"]) for row in updates}
    fields = {"parameterId", "valueId", "value", "unitId"}
    rows = [*[row for row in existing if str(row.get("parameterId")) not in changed], *updates]
    return [{key: int(value) if key == "valueId" else value for key, value in row.items() if key in fields and value is not None}
            for row in rows]

from __future__ import annotations

from .common import *

from .config_http import *
from .payloads import *

def publish_mercadolibre(payload: dict[str, Any], token: str) -> dict[str, Any]:
    item_payload = dict(payload)
    global_selling = bool(item_payload.pop("_global_selling", False)) or str(item_payload.get("category_id", "")).startswith("CBT")
    description = item_payload.get("description") if global_selling else item_payload.pop("description", None)
    if global_selling:
        item = request_json(
            "POST",
            "https://api.mercadolibre.com/global/items",
            token,
            item_payload,
            extra_headers={"parent-item-info": "true"},
        )
    else:
        item = request_json("POST", "https://api.mercadolibre.com/items", token, item_payload)
    if (not global_selling) and description and isinstance(item, dict) and item.get("id"):
        request_json(
            "POST",
            f"https://api.mercadolibre.com/items/{item['id']}/description",
            token,
            description,
        )
    return item if isinstance(item, dict) else {"response": item}


def publish_wildberries(payload: list[dict[str, Any]], token: str) -> dict[str, Any]:
    result = request_json(
        "POST",
        "https://content-api.wildberries.ru/content/v2/cards/upload",
        token,
        payload,
    )
    return result if isinstance(result, dict) else {"response": result}

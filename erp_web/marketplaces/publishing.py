from __future__ import annotations

from typing import Any

from .config_http import request_json

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

from __future__ import annotations

from typing import Any

from .config_http import request_json

def publish_mercadolibre(payload: dict[str, Any], token: str) -> dict[str, Any]:
    item_payload = dict(payload)
    item_id = str(item_payload.pop("_item_id", "") or "").strip()
    global_selling = bool(item_payload.pop("_global_selling", False)) or str(item_payload.get("category_id", "")).startswith("CBT")
    description = item_payload.get("description") if global_selling else item_payload.pop("description", None)
    if item_id:
        endpoint = (
            f"https://api.mercadolibre.com/global/items/{item_id}"
            if global_selling
            else f"https://api.mercadolibre.com/items/{item_id}"
        )
        response = request_json("PUT", endpoint, token, item_payload)
        if (not global_selling) and description:
            request_json(
                "PUT",
                f"https://api.mercadolibre.com/items/{item_id}/description",
                token,
                description,
            )
        item = dict(response) if isinstance(response, dict) else {}
        item.setdefault("id", item_id)
        item["operation"] = "updated"
    elif global_selling:
        item = request_json(
            "POST",
            "https://api.mercadolibre.com/global/items",
            token,
            item_payload,
            extra_headers={"parent-item-info": "true"},
        )
    else:
        item = request_json("POST", "https://api.mercadolibre.com/items", token, item_payload)
    if (
        not item_id
        and (not global_selling)
        and description
        and isinstance(item, dict)
        and item.get("id")
    ):
        request_json(
            "POST",
            f"https://api.mercadolibre.com/items/{item['id']}/description",
            token,
            description,
        )
    if isinstance(item, dict) and not item.get("operation"):
        item["operation"] = "created"
    return item if isinstance(item, dict) else {"response": item}

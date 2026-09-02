from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from erp_web.facades import product_facade


class _Products:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}

    def save_draft_detail(
        self, draft: dict[str, Any]
    ) -> tuple[dict[str, Any], None, int]:
        self.saved = draft
        return {"ok": True, "draft": draft}, None, 200


def test_save_draft_refreshes_ozon_description_category_id_on_target(
    monkeypatch,
) -> None:
    products = _Products()
    fetch_calls: list[dict[str, Any]] = []

    def fetch_category_record(
        platform: str,
        category_id: str,
        *,
        site: str = "",
        include_attributes: bool = False,
    ) -> dict[str, Any]:
        fetch_calls.append(
            {
                "platform": platform,
                "category_id": category_id,
                "site": site,
                "include_attributes": include_attributes,
            }
        )
        return {
            "platform": platform,
            "site": site,
            "category_id": category_id,
            "type_id": "91443",
            "description_category_id": "17039635",
        }

    monkeypatch.setattr(
        product_facade,
        "get_context",
        lambda: SimpleNamespace(products=products),
    )
    monkeypatch.setattr(
        "erp_web.runtime_units.draft_category_resolution.fetch_category_record",
        fetch_category_record,
    )

    result, status = product_facade.save_draft_payload(
        {
            "draft": {
                "draft_id": "draft-1",
                "platform": "ozon",
                "target_sites": [
                    {
                        "platform": "ozon",
                        "site": "global",
                        "category_id": "91443",
                        "description_category_id": "stale-description-id",
                    }
                ],
            }
        }
    )

    assert status == 200
    assert result["ok"] is True
    assert "description_category_id" not in products.saved
    assert (
        products.saved["target_sites"][0]["description_category_id"]
        == "17039635"
    )
    assert fetch_calls == [
        {
            "platform": "ozon",
            "category_id": "91443",
            "site": "global",
            "include_attributes": True,
        }
    ]


def test_save_draft_rejects_unresolvable_ozon_type_id(monkeypatch) -> None:
    products = _Products()
    monkeypatch.setattr(
        product_facade,
        "get_context",
        lambda: SimpleNamespace(products=products),
    )
    monkeypatch.setattr(
        "erp_web.runtime_units.draft_category_resolution.fetch_category_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("未找到 Ozon 商品类型")
        ),
    )

    result, status = product_facade.save_draft_payload(
        {
            "draft": {
                "draft_id": "draft-1",
                "platform": "ozon",
                "target_sites": [
                    {
                        "platform": "ozon",
                        "site": "global",
                        "category_id": "invalid",
                    }
                ],
            }
        }
    )

    assert status == 400
    assert result["error_code"] == "OZON_CATEGORY_PAIR_RESOLVE_FAILED"
    assert products.saved == {}

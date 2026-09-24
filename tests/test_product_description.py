"""商品仅保留事实资料，描述只在平台草稿中生成和维护。"""

import json
import sqlite3

import pytest
from pydantic import ValidationError

from erp_web.db import ErpDatabase
from erp_web.runtime_units.source_collect_parsers import (
    parse_1688_product, parse_amazon_product, parse_generic_product,
)
from erp_web.schemas.copy import LocalizedCopyOutput
from erp_web.services.copy_service import product_summary


def test_database_drops_historical_source_copy_and_preserves_draft_description(tmp_path):
    db = ErpDatabase(tmp_path / "erp.sqlite3")
    product_id = db.upsert_product_model({"name": "测试商品", "source": {"attributes": {"材质": "PP"}}})
    draft_id = db.upsert_draft_model(product_id, "ozon", {"description": "Описание"})
    # 直接模拟当前数据库中旧商品 JSON，避免通过新写入契约引入废弃字段。
    with sqlite3.connect(db.db_path) as conn:
        product = json.loads(conn.execute("SELECT product_json FROM products").fetchone()[0])
        product.update(description="平台价格说明", selling_points=["来源卖点"])
        product["source"].update(description="平台价格说明", bullets=["来源卖点"], collect_diagnostics={
            "description_found": True, "bullets_found_count": 1,
            "collected_fields": ["title", "description"], "missing_fields": ["weight", "bullets"],
        })
        conn.execute("UPDATE products SET product_json = ?", (json.dumps(product),))
        draft = json.loads(conn.execute("SELECT draft_json FROM platform_drafts").fetchone()[0])
        draft["bullets"] = ["Складной", "Складной"]
        conn.execute("UPDATE platform_drafts SET draft_json = ?", (json.dumps(draft),))
    loaded = db.load_product_model(product_id)
    assert "description" not in loaded
    assert "description" not in loaded["source"]
    assert loaded["source"]["attributes"] == {"材质": "PP"}
    diagnostics = loaded["source"]["collect_diagnostics"]
    assert "description_found" not in diagnostics
    assert diagnostics["collected_fields"] == ["title"]
    assert diagnostics["missing_fields"] == ["weight"]
    loaded_draft = db.load_draft_model(draft_id)
    assert loaded_draft["description"] == "Описание\n\nСкладной"
    assert loaded["drafts"]["ozon"]["description"] == loaded_draft["description"]
    db.upsert_product_model(loaded)
    db.upsert_draft_model(product_id, "ozon", loaded_draft)
    with sqlite3.connect(db.db_path) as conn:
        source_json, product_json = conn.execute("SELECT source_json, product_json FROM products").fetchone()
        draft_json = conn.execute("SELECT draft_json FROM platform_drafts").fetchone()[0]
    assert "description" not in json.loads(source_json)
    assert "description" not in json.loads(product_json)
    for payload in (source_json, product_json, draft_json):
        assert '"bullets"' not in payload and '"selling_points"' not in payload
    assert db.load_draft_model(draft_id)["description"] == "Описание\n\nСкладной"


@pytest.mark.parametrize("parser", [parse_1688_product, parse_amazon_product, parse_generic_product])
def test_collection_does_not_store_page_descriptions(parser):
    # 当前 1688 快照中详情组件为空，旁边是平台价格说明；两者均不作为商品描述。
    html = '''<h1>亚克力挂牌</h1><div id="description"><v-detail-b></v-detail-b>
        <div class="price-indication">平台价格说明</div></div>
        <meta name="description" content="店铺推广文案">
        <div id="productDescription">网页介绍</div>'''
    product = parser(html, "https://detail.1688.com/offer/123456.html")
    assert "description" not in product
    assert "description" not in product["source"]
    assert "description_found" not in product["source"]["collect_diagnostics"]


def test_localization_uses_facts_instead_of_historical_descriptions():
    facts = json.loads(product_summary({
        "name": "收纳盒", "description": "旧主档描述", "source_text": "平台价格说明",
        "source": {"description": "店铺推广", "attributes": {"材质": "PP"}},
        "sku_items": [{"name": "蓝色", "options": {"颜色": "蓝色"}}],
    }))
    assert facts == {"Title": "收纳盒", "Source attributes": {"材质": "PP"},
                     "SKU options": [{"name": "蓝色", "options": {"颜色": "蓝色"}}]}
    with pytest.raises(ValidationError):
        LocalizedCopyOutput(title="标题", description="")

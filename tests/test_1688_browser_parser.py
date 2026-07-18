from __future__ import annotations

from erp_web.product_model import default_collect_diagnostics, merge_source_partial_result
from erp_web.runtime_units.source_collect_parsers import extract_1688_attributes, parse_1688_product


def _sample_1688_html() -> str:
    return """
    <html>
      <head><title>2026新品恒悦笔袋 - 阿里巴巴</title></head>
      <body>
        <div id="productAttributes" data-module="od_product_attributes">
          <table>
            <tbody>
              <tr><th>品牌</th><td>恒悦</td><th>自重</th><td>25</td></tr>
              <tr><th>货号</th><td>HY25123001</td><th>原产国/地区</th><td>中国温州龙港</td></tr>
              <tr><th>款式</th><td>笔袋化妆包</td><th>规格</th><td>笔袋</td></tr>
              <tr><th>颜色</th><td colspan="3">红色牛津布 23*13高,袋身本色拉链粉色-21*13高</td></tr>
            </tbody>
          </table>
        </div>
        <script>
        window.contextPath = "/default";
        window.context=(function(b,d){return d})(window.contextPath,{
          "result": {
            "data": {
              "gallery": {
                "fields": {
                  "subject": "2026新品恒悦笔袋",
                  "mainImage": []
                }
              },
              "Root": {
                "fields": {
                  "dataJson": {
                    "tempModel": {
                      "offerTitle": "2026新品恒悦笔袋",
                      "offerId": 123456789
                    },
                    "offerBaseInfo": {"offerId": 123456789},
                    "skuModel": {
                      "skuProps": [
                        {
                          "prop": "颜色",
                          "value": [
                            {"name": "红色牛津布", "imageUrl": "https://cbu01.alicdn.com/red.jpg"},
                            {"name": "黑色牛津布", "imageUrl": "https://cbu01.alicdn.com/black.jpg"}
                          ]
                        },
                        {"prop": "规格", "value": [{"name": "笔袋"}]}
                      ],
                      "skuInfoMap": {
                        "红色牛津布&gt;笔袋": {
                          "skuId": 1001,
                          "specAttrs": "红色牛津布&gt;笔袋",
                          "discountPrice": "9.50",
                          "price": "12.00",
                          "canBookCount": 25
                        },
                        "黑色牛津布&gt;笔袋": {
                          "skuId": 1002,
                          "specAttrs": "黑色牛津布&gt;笔袋",
                          "discountPrice": "10.00",
                          "price": "12.00",
                          "canBookCount": 10
                        }
                      }
                    }
                  }
                }
              },
              "productPackInfo": {
                "fields": {
                  "pieceWeightScale": {
                    "pieceWeightScaleInfo": [
                      {"skuId": 1001, "sku1": "红色牛津布", "sku2": "笔袋", "weight": 300, "length": 20, "width": 10, "height": 3}
                    ]
                  }
                }
              }
            }
          }
        });
        </script>
      </body>
    </html>
    """


def test_extract_1688_attributes_reads_visible_attribute_table() -> None:
    attrs = extract_1688_attributes("", _sample_1688_html())

    assert attrs["品牌"] == "恒悦"
    assert attrs["货号"] == "HY25123001"
    assert attrs["原产国/地区"] == "中国温州龙港"
    assert attrs["颜色"].startswith("红色牛津布")


def test_parse_1688_product_uses_context_skus_and_preserves_attributes() -> None:
    product = parse_1688_product(
        {"html": _sample_1688_html(), "text": "", "url": "https://detail.1688.com/offer/123456789.html"},
        "https://detail.1688.com/offer/123456789.html",
    )
    source = product["source"]

    assert product["name"] == "2026新品恒悦笔袋"
    assert product["brand"] == "恒悦"
    assert product["model"] == "HY25123001"
    assert product["attributes"]["品牌"] == "恒悦"
    assert source["attributes"]["款式"] == "笔袋化妆包"
    assert source["price"] == "9.5"
    assert source["weight_kg"] == "0.3"
    assert len(source["skus"]) == 2
    assert product["sku_items"][0]["name"] == "红色牛津布 / 笔袋"
    assert product["sku_items"][0]["price"] == "9.50"
    assert product["sku_items"][0]["stock"] == "25"
    assert product["sku_items"][0]["image"] == "https://cbu01.alicdn.com/red.jpg"


def test_merge_source_partial_result_keeps_browser_1688_attributes_and_skus() -> None:
    parsed = parse_1688_product(
        {"html": _sample_1688_html(), "text": "", "url": "https://detail.1688.com/offer/123456789.html"},
        "https://detail.1688.com/offer/123456789.html",
    )
    diagnostics = default_collect_diagnostics()
    diagnostics.update({"success": True, "title_found": True, "sku_found_count": 2})

    merged = merge_source_partial_result({}, parsed["source"], diagnostics)

    assert merged["attributes"]["品牌"] == "恒悦"
    assert merged["source"]["attributes"]["货号"] == "HY25123001"
    assert merged["brand"] == "恒悦"
    assert merged["model"] == "HY25123001"
    assert len(merged["sku_items"]) == 2

"""订单详情与检索共用的纯来源解析，不读取可编辑草稿。"""

from erp_web.schemas.order_procurement import ProcurementSource, SourceSelection


def resolve_source_selection(line, key, signature, saved, candidates):
    selection = SourceSelection(line_key=key)
    unique = {}
    for candidate in candidates:
        if candidate.seller_sku != line.sku or not line.sku:
            continue
        if candidate.remote_id and line.remote_id and candidate.remote_id != line.remote_id:
            continue
        if candidate.variant_id and line.variant_id and candidate.variant_id != line.variant_id:
            continue
        unique[(candidate.product_id, candidate.sku_id, candidate.source.model_dump_json())] = candidate
    selection.candidates = list(unique.values())
    if saved:
        selection.revision = saved["revision"]
    if saved and saved["line_signature"] == signature:
        selection.status = "confirmed"
        selection.source = ProcurementSource.model_validate_json(saved["source_json"])
    elif len(unique) == 1:
        selection.status = "matched"
        selection.source = selection.candidates[0].source
        selection.reason = "已匹配发布时的来源规格，请核对后确认采购来源"
    elif len(unique) > 1:
        selection.status = "ambiguous"
        selection.reason = "找到多个发布来源，请人工选择本次采购规格"
    else:
        selection.reason = "没有可验证的发布来源，请人工关联采购商品及规格"
    return selection

"""订单详情与检索共用的纯来源解析，不读取可编辑草稿。"""

from erp_web.schemas.order_procurement import ProcurementSource, SourceSelection


def _candidate_rank(candidate):
    return (
        1 if getattr(candidate, "is_default", False) else 0,
        1 if getattr(candidate, "origin", "publish") == "alibaba_api" else 0,
        str(getattr(candidate, "updated_at", "") or ""),
    )


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
        dedupe_key = (candidate.product_id, candidate.sku_id, candidate.source.model_dump_json())
        existing = unique.get(dedupe_key)
        if existing is None or _candidate_rank(candidate) > _candidate_rank(existing):
            unique[dedupe_key] = candidate
    ordered = sorted(unique.values(), key=_candidate_rank, reverse=True)
    selection.candidates = ordered
    if saved:
        selection.revision = saved["revision"]
    defaults = [candidate for candidate in ordered if getattr(candidate, "is_default", False)]
    if saved and saved["line_signature"] == signature:
        selection.status = "confirmed"
        selection.source = ProcurementSource.model_validate_json(saved["source_json"])
    elif len(defaults) == 1:
        selection.status = "matched"
        selection.source = defaults[0].source
        selection.reason = (
            "已匹配该 SKU 默认采购源，核对后可确认采购或切换备选"
            if len(ordered) > 1
            else "已匹配该 SKU 默认采购源，请核对后确认采购来源"
        )
    elif len(ordered) == 1:
        selection.status = "matched"
        selection.source = ordered[0].source
        selection.reason = "已匹配发布时的来源规格，请核对后确认采购来源"
    elif len(ordered) > 1:
        selection.status = "ambiguous"
        selection.reason = "找到多个采购来源，请选择或设为该 SKU 默认采购规格"
    else:
        selection.reason = "尚未关联采购来源，可粘贴 1688 链接点选规格绑定"
    return selection

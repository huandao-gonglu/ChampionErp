"""所选商品的批量操作：本地范围预览、原子入队与平台原生批量写入。"""
from erp_web.marketplaces.publisher import PublishAdapterError
from erp_web.product_model.online_operation_model import receipt_errors, submission_status
from erp_web.schemas.online_products import BatchChangePreviewRequest, BatchChangeRequest, ChangeRequest, digest, snapshot_version
from erp_web.services.online_product_changes import validate_changes
from erp_web.services.online_product_listing import _matching_groups
from erp_web.stores.online_product_store import OnlineConflict


def scope_for(operation):
    return "business" if operation == "archive" else "global"


def submission_result(jobs):
    """批量入队回执不重复返回每件商品的完整修改前快照。"""
    return {"ok": True, "jobs": [{**{key: job[key] for key in (
        "id", "operation", "platform", "status", "created_at", "updated_at", "target_id")},
        "request": {}, "result": {"batch_id": job["result"]["batch_id"]}} for job in jobs]}


def preview_batch(service, body):
    request = BatchChangePreviewRequest.model_validate(body)
    records, _ = service._list_context(request.platform, request.catalog)
    wanted_groups, wanted_items = set(request.group_ids), set(request.listing_ids)
    found_groups, found_items, items, errors = set(), set(), [], []
    for group, members in _matching_groups(records, query="", status="", market=""):
        for row in members:
            if group.id not in wanted_groups and row.id not in wanted_items:
                continue
            found_groups.add(group.id)
            found_items.add(row.id)
            command = ChangeRequest(listing_id=row.id, version=row.version, operation=request.operation,
                                    scope_id=scope_for(request.operation), changes=request.changes, idempotency_key="batch-preview")
            try:
                validate_changes(row, command)
            except ValueError as exc:
                errors.append({"listing_id": row.id, "seller_sku": row.seller_sku, "error": str(exc)})
            items.append({"listing_id": row.id, "version": row.version, "seller_sku": row.seller_sku,
                          "title": row.title, "group_id": group.id})
    if wanted_groups - found_groups or wanted_items - found_items:
        raise OnlineConflict("所选商品已不在当前目录，请刷新后重新选择")
    if not items or len(items) > 5000:
        raise ValueError("请选择 1–5000 个 SKU")
    return {"ok": True, "items": items, "errors": errors, "product_count": len(found_groups),
            "scope": "商家账号全部店铺" if request.operation == "archive" else "当前店铺"}


def submit_batch(service, body):
    request = BatchChangeRequest.model_validate(body)
    batch_id = digest(["online-batch", request.idempotency_key])
    fingerprint = digest(request.model_dump(exclude={"idempotency_key"}))
    old = service.store.batch_jobs(batch_id)
    if old:
        for job in old:
            service._config(job["platform"], job["account_id"])
            if job["result"].get("batch_fingerprint") != fingerprint or len(old) != len(request.items):
                raise OnlineConflict("批量提交键对应不同的商品或修改，请重新确认")
        return submission_result(old)
    listings = [service.store.get(item.listing_id) for item in request.items]
    platform, account = listings[0].platform, listings[0].account_id
    if platform != "yandex" or any((row.platform, row.account_id) != (platform, account) for row in listings):
        raise ValueError("批量停售和归档仅支持同一 Yandex 店铺的商品")
    adapter = service._adapter(platform, account)
    commands = []
    for index, (item, listing) in enumerate(zip(request.items, listings, strict=True)):
        if listing.version != item.version:
            raise OnlineConflict(f"商品 {listing.seller_sku} 已变化，请重新预览")
        command = ChangeRequest(**item.model_dump(), operation=request.operation, changes=request.changes,
                                scope_id=scope_for(request.operation), idempotency_key=f"batch:{batch_id}:{index:05d}")
        validate_changes(listing, command)
        commands.append(command.model_dump())
    jobs = service.store.enqueue_batch(platform, account, commands, batch_id=batch_id,
                                        fingerprint=fingerprint, limit=adapter.batch_limits[request.operation])
    service._wake.set()
    return submission_result(jobs)


def run_batch(service, jobs):
    entries = []
    dispatched = False
    try:
        adapter = service._adapter(jobs[0]["platform"], jobs[0]["account_id"])
        before = {job["id"]: service.store.get(job["target_id"]) for job in jobs}
        batch = adapter.read_many([row.remote_id for row in before.values()])
        fresh = {row.remote_id: row for row in batch.listings}
        for job in jobs:
            listing = before[job["id"]]
            result = {**job["result"], "before": listing.model_dump(exclude={"snapshot"}),
                      "changes": job["request"]["changes"], "scope_id": job["request"]["scope_id"], "request_job_id": jobs[0]["id"]}
            try:
                current = fresh.get(listing.remote_id)
                if current is None or current.errors or listing.remote_id in batch.errors:
                    raise ValueError(batch.errors.get(listing.remote_id) or "平台未完整返回该商品，未提交修改")
                current.desired_sale_state = listing.desired_sale_state
                if snapshot_version(current) != job["request"]["version"]:
                    service.store.save(current, lease=job)
                    raise OnlineConflict("平台商品已变化，未提交修改；请重新预览")
                validate_changes(current, ChangeRequest.model_validate(job["request"]))
                entries.append((job, current, result))
            except ValueError as exc:
                service._update(job, "failed", {**result, "error": str(exc)})
        if not entries:
            return True
        service._config(jobs[0]["platform"], jobs[0]["account_id"])
        service.store.dispatch_batch(entries)
        dispatched = True
        command = entries[0][0]["request"]
        receipts = adapter.write_batch([row for _, row, _ in entries], command["operation"], command["scope_id"], command["changes"])
        if set(receipts) != {row.remote_id for _, row, _ in entries}:
            raise ValueError("批量回执未覆盖全部目标，结果需核对")
        for job, listing, result in entries:
            result["receipt"] = receipts[listing.remote_id]
        # 先持久化每项实际回执，再更新快照；中断后可按原回执核验，不重放批量写入。
        for job, _, result in entries:
            service._update(job, "running", result, dispatched=True)
        for job, listing, result in entries:
            status = submission_status(job["platform"], job["operation"], result["receipt"])
            if status in ("failed", "partial"):
                result["platform_errors"] = receipt_errors(result["receipt"])
            if status == "confirmed" and job["operation"] == "archive":
                service.store.archive_state(listing.id, command["changes"]["archived"], lease=job)
                result["platform_confirmation"] = {"note": "平台已确认归档操作成功；买家端展示更新可能稍有延迟"}
            if job["operation"] == "sale_state":
                if status == "failed":
                    service.store.sale_intent(listing.id, before[job["id"]].desired_sale_state, lease=job)
                elif status == "submitted":
                    service.store.sale_intent(listing.id, command["changes"]["state"], lease=job)
            service._update(job, status, result, dispatched=True)
        return True
    except Exception as exc:
        code = int(exc.details.get("http_status") or 0) if isinstance(exc, PublishAdapterError) else 0
        rejected = isinstance(exc, PublishAdapterError) and (exc.details.get("definitively_rejected") is True or 400 <= code < 500 and code not in (408, 425))
        prepared = {job["id"]: (listing, result) for job, listing, result in entries}
        affected = [(job, *prepared.get(job["id"], (None, job["result"]))) for job in jobs]
        for job, listing, result in affected:
            # 已写入终态的成员不因另一项本地保存失败而倒退。
            if service.store.job(job["id"])["status"] != "running":
                continue
            if rejected and listing and job["operation"] == "sale_state":
                service.store.sale_intent(listing.id, result["before"]["desired_sale_state"], lease=job)
            sent = dispatched and job["id"] in prepared
            service._update(job, "outcome_unknown" if sent and not rejected else "failed",
                            {**result, "error": str(exc)}, dispatched=sent)
        return True

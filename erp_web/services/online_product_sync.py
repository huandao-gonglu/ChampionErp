"""在线同步批次落库与进度；平台请求由适配器负责，不持有商品写锁等待网络。"""
from erp_web.schemas.online_products import OnlineSyncBatch


def run_sync(service, job, adapter, result):
    known = {row.remote_id: row.id for row in service.store.listings(job["platform"], job["account_id"])}
    identities = dict(known)
    discovered, pending = set(), set()
    result.update(phase="catalog", discovered=0, discovery_complete=False)
    service._update(job, "running", result)

    def failure(remote_id, error):
        if remote_id in identities:
            service.store.sync_state(identities[remote_id], "failed", [error], lease=job)
        pending.discard(remote_id)
        result["failed"] += 1
        result["items"].append({"remote_id": remote_id, "status": "failed", "error": error})

    try:
        batches = iter(adapter.sync(job["request"].get("ids")))
        while not service._stop.is_set():
            try:
                batch: OnlineSyncBatch = next(batches)
            except StopIteration:
                break
            if service._stop.is_set():
                break
            # 店铺切换时停止落库；不允许长同步继续写入已切换账号的快照。
            service._config(job["platform"], job["account_id"])
            result["phase"] = batch.phase
            if batch.discovery_complete:
                result["discovery_complete"] = True
            for listing in batch.listings:
                if listing.platform != job["platform"] or listing.account_id != job["account_id"]:
                    raise ValueError("同步批次商品与当前店铺身份不一致")
                remote_id = listing.remote_id
                discovered.add(remote_id)
                identities[remote_id] = listing.id
                if batch.phase == "catalog":
                    if remote_id not in known:
                        service.store.save(listing, lease=job)
                    else:
                        service.store.sync_state(listing.id, "pending", [], lease=job)
                    pending.add(remote_id)
                    continue
                if listing.errors:
                    if remote_id not in known:
                        listing.details_state = "failed"
                        service.store.save(listing, lease=job)
                    failure(remote_id, "；".join(listing.errors))
                    continue
                listing.details_state = "ready"
                service.store.save(listing, lease=job)
                pending.discard(remote_id)
                result["created" if remote_id not in known else "updated"] += 1
                result["completed"] += 1
                result["items"].append({"remote_id": remote_id, "status": "confirmed"})
            for remote_id, error in batch.errors.items():
                discovered.add(remote_id)
                failure(remote_id, error)
            result["discovered"] = len(discovered)
            service._update(job, "running", result)
        if service._stop.is_set():
            service._update(job, "queued", result)
            return
        for remote_id in sorted(pending):
            failure(remote_id, "平台未返回该商品的完整详情，请重试失败项")
        result.update(discovery_complete=True, phase="complete")
        service._update(job, "partial" if result["failed"] else "confirmed", result)
    except Exception as exc:
        # 发现分页或批次中断时，把已展示但未完成的记录留下，并给出失败原因。
        for remote_id in sorted(pending):
            failure(remote_id, str(exc))
        raise

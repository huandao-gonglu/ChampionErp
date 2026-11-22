"""在线同步批次落库与进度；平台请求由适配器负责，不持有商品写锁等待网络。"""
import logging

from erp_web.marketplaces.online_sync import raise_if_access_blocked
from erp_web.schemas.online_products import OnlineSyncBatch

logger = logging.getLogger(__name__)


def _reconcile_missing(service, job, adapter, catalog: str, catalog_snapshot: dict[str, str], discovered: set[str], result: dict) -> set[str]:
    """全量目录同步或热刷幽灵复核后，对缺失 SKU 做平台复核、目录迁移与安全清理；返回实际已清理或迁移的 remote_id。"""
    missing = sorted(set(catalog_snapshot) - discovered)
    if not missing:
        return set()
    moved_skus: set[str] = set()
    if hasattr(adapter, "reconcile_missing"):
        try:
            removed_skus, moved_map = adapter.reconcile_missing(missing, catalog=catalog)
        except Exception as exc:
            raise_if_access_blocked(exc)
            logger.warning("同步目录对账复核失败，保留未发现记录：%s", exc)
            return set()
        if service._stop.is_set():
            return set()
        service._config(job["platform"], job["account_id"])
        for sku, new_archived in moved_map.items():
            listing_id = catalog_snapshot.get(sku)
            if not listing_id:
                continue
            try:
                service.store.archive_state(listing_id, new_archived, lease=job)
                moved_skus.add(sku)
            except ValueError:
                continue
        stale_by_id = {catalog_snapshot[sku]: sku for sku in removed_skus if sku in catalog_snapshot}
    else:
        stale_by_id = {catalog_snapshot[sku]: sku for sku in missing if sku in catalog_snapshot}
    removed_ids = service.store.prune_missing_ids(job["platform"], job["account_id"], list(stale_by_id), lease=job) if stale_by_id else []
    removed_skus_done = {stale_by_id[listing_id] for listing_id in removed_ids if listing_id in stale_by_id}
    if removed_skus_done:
        result["removed"] = int(result.get("removed") or 0) + len(removed_skus_done)
    if moved_skus:
        result["moved"] = int(result.get("moved") or 0) + len(moved_skus)
    return removed_skus_done | moved_skus


def run_sync(service, job, adapter, result):
    catalog = job["request"].get("catalog", "current")
    mode = job["request"].get("mode", "full")
    records = service.store.listings(job["platform"], job["account_id"])
    known = {row.remote_id: row.id for row in records}
    # 失败重试中的商品可能已移入另一目录；不得把另一目录快照标记为同步失败。
    identities = {row.remote_id: row.id for row in records if row.archived is (catalog == "archived")}
    catalog_snapshot = dict(identities) if job["platform"] == "yandex" else dict(known)
    discovered, pending = set(), set()
    hot_missing_candidates: set[str] = set()
    adapter_discovery_complete = False
    if mode == "hot":
        target_ids = list(dict.fromkeys(job["request"].get("ids") or list(identities)))
        result.update(phase="hot", data_scope="hot", discovered=len(target_ids), discovery_complete=True)
        service._update(job, "running", result)
        if not target_ids:
            result.update(phase="complete")
            service._update(job, "confirmed", result)
            return
        pending.update(target_ids)
        discovered.update(target_ids)
    else:
        result.update(phase="catalog", discovered=0, discovery_complete=False)
        if job["platform"] == "yandex":
            result["data_scope"] = "listing"
        service._update(job, "running", result)

    def failure(remote_id, error):
        if mode != "hot" and remote_id in identities:
            service.store.sync_state(identities[remote_id], "failed", [error], lease=job)
        pending.discard(remote_id)
        result["failed"] += 1
        result["items"].append({"remote_id": remote_id, "status": "failed", "error": error})

    batches = None
    try:
        batches = iter(adapter.sync_hot(target_ids) if mode == "hot" else adapter.sync(job["request"].get("ids"), catalog=catalog))
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
                adapter_discovery_complete = True
                result["discovery_complete"] = True
            if batch.missing_candidates:
                hot_missing_candidates.update(batch.missing_candidates)
            hot_batch_items = []
            basic_batch_items = []
            detail_batch_items = []
            catalog_new_items = []
            catalog_pending_ids = []
            for listing in batch.listings:
                if listing.platform != job["platform"] or listing.account_id != job["account_id"]:
                    raise ValueError("同步批次商品与当前店铺身份不一致")
                remote_id = listing.remote_id
                discovered.add(remote_id)
                identities[remote_id] = listing.id
                if batch.phase == "catalog":
                    if remote_id not in known:
                        catalog_new_items.append(listing)
                    elif job["platform"] != "yandex":
                        catalog_pending_ids.append(listing.id)
                    pending.add(remote_id)
                    continue
                if listing.errors:
                    if mode != "hot" and remote_id not in known:
                        listing.details_state = "failed"
                        service.store.save(listing, lease=job)
                    failure(remote_id, "；".join(listing.errors))
                    continue
                if batch.phase == "hot":
                    hot_batch_items.append(listing)
                    continue
                if batch.phase == "basic":
                    basic_batch_items.append(listing)
                    continue
                listing.details_state = "ready"
                detail_batch_items.append(listing)
            if catalog_new_items:
                service.store.save_many(catalog_new_items, lease=job, full_snapshot=job["platform"] != "yandex")
            if catalog_pending_ids:
                service.store.sync_state_many(catalog_pending_ids, "pending", [], lease=job)
            if detail_batch_items:
                service.store.save_many(detail_batch_items, lease=job, full_snapshot=True)
                for listing in detail_batch_items:
                    remote_id = listing.remote_id
                    pending.discard(remote_id)
                    result["created" if remote_id not in known else "updated"] += 1
                    result["completed"] += 1
                    result["items"].append({"remote_id": remote_id, "status": "confirmed"})
            if hot_batch_items:
                saved_map = service.store.save_hot_many(hot_batch_items, lease=job)
                for listing in hot_batch_items:
                    remote_id = listing.remote_id
                    saved = saved_map.get(listing.id)
                    if saved is None:
                        pending.discard(remote_id)
                        continue
                    sales_errors = list(dict.fromkeys(s.read_error for s in (*saved.prices, *saved.stocks) if s.read_error))
                    if sales_errors:
                        pending.discard(remote_id)
                        result["failed"] += 1
                        result["items"].append({"remote_id": remote_id, "status": "failed", "error": "；".join(sales_errors)})
                        continue
                    pending.discard(remote_id)
                    result["created" if remote_id not in known else "updated"] += 1
                    result["completed"] += 1
                    result["items"].append({"remote_id": remote_id, "status": "confirmed"})
            if basic_batch_items:
                saved_list = service.store.save_basic_many(basic_batch_items, lease=job)
                for saved in saved_list:
                    remote_id = saved.remote_id
                    sales_errors = list(dict.fromkeys(s.read_error for s in (*saved.prices, *saved.stocks) if s.read_error))
                    if sales_errors:
                        pending.discard(remote_id)
                        result["failed"] += 1
                        result["items"].append({"remote_id": remote_id, "status": "failed", "error": "；".join(sales_errors)})
                        continue
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
        if mode == "hot" and hot_missing_candidates and job["request"].get("ids") is None and hasattr(adapter, "reconcile_missing"):
            present_skus = set(catalog_snapshot) - hot_missing_candidates
            reconciled = _reconcile_missing(service, job, adapter, catalog, catalog_snapshot, present_skus, result)
            if reconciled:
                result["items"] = [
                    item for item in result["items"]
                    if not (item.get("status") == "failed" and item.get("remote_id") in reconciled)
                ]
                result["failed"] = sum(1 for item in result["items"] if item.get("status") == "failed")
        elif mode != "hot" and job["request"].get("ids") is None and adapter_discovery_complete:
            _reconcile_missing(service, job, adapter, catalog, catalog_snapshot, discovered, result)
            if service._stop.is_set():
                service._update(job, "queued", result)
                return
        result.update(discovery_complete=True, phase="complete")
        status = "confirmed" if not result["failed"] else ("partial" if result["completed"] else "failed")
        service._update(job, status, result)
    except Exception as exc:
        # 发现分页或批次中断时，把已展示但未完成的记录留下，并给出失败原因。
        for remote_id in sorted(pending):
            failure(remote_id, str(exc))
        raise
    finally:
        if batches is not None and hasattr(batches, "close"):
            batches.close()

"""发布图片的唯一交付边界：外部直链与当前默认 S3 目标。"""
from __future__ import annotations

import urllib.parse
from copy import deepcopy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from threading import BoundedSemaphore, local
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from erp_web.schemas.publish import PublishPreparationObserver
from erp_web.context import AppPaths
from erp_web.product_model import draft_image_asset_ids, normalize_image_pool
from erp_web.product_model.sku_model import selected_skus
from erp_web.product_model.sku_image_model import sku_image_asset
from erp_web.schemas.image_hosting import DeliveredImage, ImageHostingError
from erp_web.services.image_hosting_config import config_version, content_storage_key, default_profile, public_url, target_fingerprint, validate_public_url
from erp_web.services.image_content import MAX_IMAGE_BYTES, image_content, read_image_file
from erp_web.services.s3_image_storage import S3ImageStorage
from erp_web.services.watermark_assets import publication_image
from erp_web.services.watermark_service import has_watermark_content, watermark_fingerprint, watermark_settings


# 所有素材准备请求共用传输上限；每个线程单独装配 SDK 客户端。
IMAGE_DELIVERY_WORKERS = 4
_delivery_slots = BoundedSemaphore(IMAGE_DELIVERY_WORKERS)


@dataclass(frozen=True)
class ImageDeliveryProblem:
    asset_id: str
    code: str
    message: str
    next_action: str


@dataclass
class ImageInspectionCache:
    """仅在一次发布校验中复用同一资产的检查；下次校验必须重新读取。"""
    profile: dict | None = field(default=None, repr=False)
    results: dict[tuple[str, str], tuple[dict, ImageDeliveryProblem | None]] = field(default_factory=dict)


class ImageHttpsProvider(Protocol):
    name: str

    def deliver(self, *, source_path: Path | None, storage_key: str, content_sha256: str) -> DeliveredImage: ...


def build_image_https_provider(profile: dict) -> ImageHttpsProvider:
    # 第一版只有一个托管适配器；外部直链是素材来源，不能注册为托管类型。
    return S3ImageStorage(profile)


class ImageDeliveryService:
    def __init__(self, paths: AppPaths, config_loader: Callable[[], dict], provider_factory=build_image_https_provider):
        self.paths = paths
        self.config_loader = config_loader
        self.provider_factory = provider_factory

    def current_profile(self) -> dict:
        return default_profile(self.config_loader())

    def inspect_mercado_watermarks(self, product: dict, *, stage: str = "final") -> list[ImageDeliveryProblem]:
        watermark = watermark_settings(self.config_loader())
        fingerprint = watermark_fingerprint(watermark)
        pool = normalize_image_pool(product.get("source", {}).get("image_pool", []))
        ids = self._target_asset_ids(product, pool, "mercadolibre")
        problems = []
        for item in pool:
            if item["id"] not in ids:
                continue
            previous = item.get("platform_uploads", {}).get("mercadolibre", {})
            if not fingerprint and not previous.get("watermark_fingerprint"):
                continue
            path = self._source_path(item)
            stale = previous.get("watermark_fingerprint", "") != fingerprint
            try:
                if path is not None and fingerprint:
                    stale = stale or image_content(read_image_file(path))[0] != previous.get("source_sha256")
                if stage == "source":
                    if path is None and stale:
                        raise ImageHostingError("IMAGE_SOURCE_MISSING", "原图不存在，无法更新水印，请重新导入原图")
                elif stale:
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片水印或原图已变化，请重新准备发布图片")
            except (ImageHostingError, OSError) as exc:
                problems.append(ImageDeliveryProblem(item["id"], getattr(exc, "code", "IMAGE_SOURCE_MISSING"), str(exc), "重新准备发布图片"))
        return problems

    def _source_path(self, item: dict) -> Path | None:
        for key in ("path", "preview_url", "url"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            if value.startswith("/file?"):
                value = urllib.parse.parse_qs(urllib.parse.urlparse(value).query).get("path", [""])[0]
            elif value.startswith("file:"):
                value = urllib.parse.unquote(urllib.parse.urlparse(value).path)
            elif value.startswith(("http://", "https://", "ml-id:", "data:", "blob:")):
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = self.paths.app_dir / candidate
            from .workspace_files import owned_path
            candidate = owned_path(candidate, app_dir=self.paths.app_dir)
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _target_asset_ids(product: dict, pool: list[dict], platform: str) -> set[str]:
        draft = product.get("drafts", {}).get(platform, {})
        ids = set(draft_image_asset_ids(draft.get("images")))
        for fact, _ in selected_skus(product, draft):
            asset = sku_image_asset(product, fact)
            if asset:
                ids.add(str(asset["id"]))
        if ids:
            return ids
        candidates = [item for item in pool if not item.get("platforms") or platform in item["platforms"]]
        selected = [item for item in candidates if item.get("selected")]
        return {item["id"] for item in selected or candidates}

    @staticmethod
    def _external(item: dict) -> bool:
        # 有本地源身份或托管标记的 URL 属于项目管理的交付结果。
        return bool(str(item.get("url") or "").startswith(("http://", "https://"))
                    and not any(item.get(field) for field in ("path", "storage_key", "delivery_provider", "hosting_profile_id", "delivery_fingerprint")))

    def _inspect_image(self, item: dict, stage: str, cache: ImageInspectionCache) -> None:
        watermark = watermark_settings(self.config_loader())
        fingerprint_watermark = watermark_fingerprint(watermark)
        if self._external(item) and not has_watermark_content(watermark):
            validate_public_url(item["url"])
            return
        if stage != "source" and item.get("watermark_fingerprint", "") != fingerprint_watermark:
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "水印设置已变化，请重新准备发布图片")
        path = self._source_path(item)
        if str(item.get("url") or "").startswith(("data:", "blob:", "ml-id:")) and path is None:
            raise ImageHostingError("IMAGE_NOT_PUBLIC", "该图片地址不能直接用于平台发布", "导入有效本地图片后准备")
        if cache.profile is None:
            cache.profile = self.current_profile()
        profile = cache.profile
        fingerprint = target_fingerprint(profile)
        same_target = (item.get("delivery_provider") == "s3_compatible" and item.get("hosting_profile_id") == profile["id"]
                       and item.get("delivery_fingerprint") == fingerprint)
        remote_source = item.get("watermark_source_url") or (item.get("url") if self._external(item) else "")
        if path is None and not same_target and not remote_source:
            raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片不存在，无法准备当前托管目标", "重新导入源图片")
        if stage == "source":
            if remote_source:
                validate_public_url(remote_source)
                return
            if path is None and item.get("watermark_fingerprint", "") != fingerprint_watermark:
                raise ImageHostingError("IMAGE_SOURCE_MISSING", "原图不存在，无法更新水印，请重新导入原图")
            if path is not None:
                # 基础预检只检查可读取与大小；完整解码由素材准备统一执行。
                with path.open("rb") as stream:
                    if not stream.read(1) or path.stat().st_size > MAX_IMAGE_BYTES:
                        raise ImageHostingError("IMAGE_SOURCE_MISSING", "图片为空或超过 20 MiB 上限", "导入有效图片后重新准备")
            if path is None and not (item.get("storage_key") and item.get("content_sha256")):
                raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片与可复用交付记录均不存在")
            return
        if not same_target and item.get("delivery_provider"):
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片交付与当前默认托管目标不一致")
        if not same_target or not item.get("storage_key") or not item.get("content_sha256"):
            raise ImageHostingError("IMAGE_NOT_PREPARED", "图片尚未准备", "调用 product_publish_prepare 上传图片后重新校验")
        expected_key = content_storage_key(profile, item["content_sha256"], item["storage_key"].rsplit(".", 1)[-1])
        if item["storage_key"] != expected_key:
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片对象 key 与内容身份不一致", "重新准备图片")
        if item.get("url") != public_url(profile, item["storage_key"]):
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片交付地址已失效")
        source_sha = item.get("delivery_source_sha256") if fingerprint_watermark else item["content_sha256"]
        if path is not None and image_content(read_image_file(path))[0] != source_sha:
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "本地图片内容已变化，请重新准备")

    def inspect_product(self, product: dict, platform: str, *, stage: str = "final", cache: ImageInspectionCache | None = None) -> list[ImageDeliveryProblem]:
        """源校验允许待上传素材；最终校验要求交付与当前目标一致，全程无网络。"""
        cache = cache if cache is not None else ImageInspectionCache()
        pool = normalize_image_pool(product.get("source", {}).get("image_pool", []))
        ids = self._target_asset_ids(product, pool, platform)
        problems = []
        for item in pool:
            if item["id"] not in ids:
                continue
            key = (stage, item["id"])
            cached = cache.results.get(key)
            if cached is not None and cached[0] == item:
                problem = cached[1]
            else:
                problem = None
                try:
                    self._inspect_image(item, stage, cache)
                except (ImageHostingError, OSError) as exc:
                    problem = ImageDeliveryProblem(item["id"], getattr(exc, "code", "IMAGE_SOURCE_MISSING"), str(exc), getattr(exc, "next_action", "重新导入图片"))
                cache.results[key] = (deepcopy(item), problem)
            if problem is not None:
                problems.append(problem)
        for asset_id in sorted(ids - {item["id"] for item in pool}):
            problems.append(ImageDeliveryProblem(asset_id, "IMAGE_SOURCE_MISSING", "草稿引用的图片不在图片池中", "从图片池重新选择图片"))
        return problems

    def prepare_product(self, product: dict, platform: str, *, on_progress: PublishPreparationObserver | None = None) -> dict:
        watermark = watermark_settings(self.config_loader())
        fingerprint_watermark = watermark_fingerprint(watermark)
        prepared = deepcopy(product)
        pool = normalize_image_pool(prepared.get("source", {}).get("image_pool", []))
        ids = self._target_asset_ids(prepared, pool, platform)
        completed_count = 0
        if on_progress:
            on_progress({"stage": "uploading", "completed": 0, "total": len(ids)})
        pending_items = []
        for item in pool:
            if item["id"] not in ids:
                continue
            if self._external(item) and not has_watermark_content(watermark):
                validate_public_url(item["url"])
                completed_count += 1
                if on_progress:
                    on_progress({"stage": "uploading", "completed": completed_count, "total": len(ids)})
            else:
                pending_items.append(item)
        if not pending_items:
            prepared.setdefault("source", {})["image_pool"] = pool
            return prepared
        profile = self.current_profile()
        providers = local()

        def deliver(item: dict) -> dict:
            with _delivery_slots:
                if not hasattr(providers, "provider"):
                    providers.provider = self.provider_factory(profile)
                source_path = self._source_path(item)
                remote_source = str(item.get("watermark_source_url") or (item.get("url") if self._external(item) else "") or "")
                if has_watermark_content(watermark) or (item.get("watermark_fingerprint") and remote_source):
                    with publication_image(source_path, remote_source, watermark) as (target, source_sha):
                        delivered = providers.provider.deliver(source_path=target, storage_key="", content_sha256="")
                        if delivered.content_sha256 != image_content(read_image_file(target))[0]:
                            raise ImageHostingError("IMAGE_DELIVERY_STALE", "水印交付内容不一致，请重新准备")
                    return {**delivered.fields(), "watermark_fingerprint": fingerprint_watermark,
                            "delivery_source_sha256": source_sha, "watermark_source_url": remote_source}
                if item.get("watermark_fingerprint") and source_path is None:
                    raise ImageHostingError("IMAGE_SOURCE_MISSING", "原图不存在，无法移除水印，请重新导入原图")
                before_sha256 = image_content(read_image_file(source_path))[0] if source_path else ""
                if source_path is None:
                    if item.get("hosting_profile_id") != profile["id"] or item.get("delivery_fingerprint") != target_fingerprint(profile):
                        raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片不存在，不能切换托管目标")
                delivered = providers.provider.deliver(source_path=source_path, storage_key=str(item.get("storage_key") or ""), content_sha256=str(item.get("content_sha256") or ""))
                if source_path is not None and (before_sha256 != delivered.content_sha256 or image_content(read_image_file(source_path))[0] != delivered.content_sha256):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间源图片内容已变化，请重新准备")
                fields = delivered.fields()
                if item.get("watermark_fingerprint"):
                    fields.update(watermark_fingerprint="", delivery_source_sha256="", watermark_source_url="")
                return fields

        # 只保留至多四个在途任务；发生错误后不再派发剩余图片。
        # 返回前等待已发出的请求结束，原请求审计上下文传入每个工作线程。
        items = iter(pending_items)
        with ThreadPoolExecutor(max_workers=IMAGE_DELIVERY_WORKERS, thread_name_prefix="image-delivery") as executor:
            pending = {}
            def submit_next() -> None:
                item = next(items, None)
                if item is not None:
                    pending[executor.submit(copy_context().run, deliver, item)] = item
            for _ in range(IMAGE_DELIVERY_WORKERS):
                submit_next()
            try:
                while pending:
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    results = [(future, future.result()) for future in completed]
                    for future, fields in results:
                        item = pending.pop(future)
                        item.update(fields)
                        item.pop("delivery_error", None)
                        completed_count += 1
                        if on_progress:
                            on_progress({"stage": "uploading", "completed": completed_count, "total": len(ids)})
                    for _ in completed:
                        submit_next()
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
        try:
            if watermark_fingerprint(watermark_settings(self.config_loader())) != fingerprint_watermark:
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间水印设置已变化，请重新准备")
            current = self.current_profile()
            if current["id"] != profile["id"] or config_version(current) != config_version(profile):
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间托管配置已变化，请重新准备")
        except ImageHostingError:
            raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间图片托管或水印设置已变化，请重新准备") from None
        prepared.setdefault("source", {})["image_pool"] = pool
        return prepared


__all__ = ["ImageDeliveryService", "ImageDeliveryProblem", "ImageHttpsProvider", "build_image_https_provider"]

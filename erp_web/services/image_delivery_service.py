"""发布图片的唯一交付边界：外部直链与当前默认 S3 目标。"""
from __future__ import annotations

import urllib.parse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from erp_web.context import AppPaths
from erp_web.product_model import draft_image_asset_ids, normalize_image_pool
from erp_web.product_model.sku_model import selected_skus
from erp_web.product_model.sku_image_model import sku_image_asset
from erp_web.schemas.image_hosting import DeliveredImage, ImageHostingError
from erp_web.services.image_hosting_config import config_version, content_storage_key, default_profile, public_url, target_fingerprint, validate_public_url
from erp_web.services.image_content import image_content, read_image_file
from erp_web.services.s3_image_storage import S3ImageStorage


@dataclass(frozen=True)
class ImageDeliveryProblem:
    asset_id: str
    code: str
    message: str
    next_action: str


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

    def inspect_product(self, product: dict, platform: str, *, stage: str = "final") -> list[ImageDeliveryProblem]:
        """源校验允许待上传素材；最终校验要求交付与当前目标一致，全程无网络。"""
        pool = normalize_image_pool(product.get("source", {}).get("image_pool", []))
        ids = self._target_asset_ids(product, pool, platform)
        problems = []
        profile = None
        for item in pool:
            if item["id"] not in ids:
                continue
            try:
                if self._external(item):
                    validate_public_url(item["url"])
                    continue
                if str(item.get("url") or "").startswith(("data:", "blob:", "ml-id:")) and self._source_path(item) is None:
                    raise ImageHostingError("IMAGE_NOT_PUBLIC", "该图片地址不能直接用于平台发布", "导入有效本地图片后准备")
                if profile is None:
                    profile = self.current_profile()
                fingerprint = target_fingerprint(profile)
                same_target = (item.get("delivery_provider") == "s3_compatible" and item.get("hosting_profile_id") == profile["id"]
                               and item.get("delivery_fingerprint") == fingerprint)
                path = self._source_path(item)
                if path is None and not same_target:
                    raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片不存在，无法准备当前托管目标", "重新导入源图片")
                if stage == "source":
                    if path is not None:
                        image_content(read_image_file(path))
                    if path is None and not (item.get("storage_key") and item.get("content_sha256")):
                        raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片与可复用交付记录均不存在")
                    continue
                if not same_target and item.get("delivery_provider"):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片交付与当前默认托管目标不一致")
                if not same_target or not item.get("storage_key") or not item.get("content_sha256"):
                    raise ImageHostingError("IMAGE_NOT_PREPARED", "图片尚未准备", "调用 product_publish_prepare 上传图片后重新校验")
                expected_key = content_storage_key(profile, item["content_sha256"], item["storage_key"].rsplit(".", 1)[-1])
                if item["storage_key"] != expected_key:
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片对象 key 与内容身份不一致", "重新准备图片")
                if item.get("url") != public_url(profile, item["storage_key"]):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "图片交付地址已失效")
                if path is not None and image_content(read_image_file(path))[0] != item["content_sha256"]:
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "本地图片内容已变化，请重新准备")
            except (ImageHostingError, OSError) as exc:
                problems.append(ImageDeliveryProblem(item["id"], getattr(exc, "code", "IMAGE_SOURCE_MISSING"), str(exc), getattr(exc, "next_action", "重新导入图片")))
        for asset_id in sorted(ids - {item["id"] for item in pool}):
            problems.append(ImageDeliveryProblem(asset_id, "IMAGE_SOURCE_MISSING", "草稿引用的图片不在图片池中", "从图片池重新选择图片"))
        return problems

    def prepare_product(self, product: dict, platform: str) -> dict:
        prepared = deepcopy(product)
        pool = normalize_image_pool(prepared.get("source", {}).get("image_pool", []))
        ids = self._target_asset_ids(prepared, pool, platform)
        profile = None
        provider = None
        for item in pool:
            if item["id"] not in ids:
                continue
            if self._external(item):
                validate_public_url(item["url"])
                continue
            if profile is None:
                profile = self.current_profile()
                provider = self.provider_factory(profile)
            source_path = self._source_path(item)
            before_sha256 = image_content(read_image_file(source_path))[0] if source_path else ""
            if source_path is None:
                if item.get("hosting_profile_id") != profile["id"] or item.get("delivery_fingerprint") != target_fingerprint(profile):
                    raise ImageHostingError("IMAGE_SOURCE_MISSING", "源图片不存在，不能切换托管目标")
            delivered = provider.deliver(source_path=source_path, storage_key=str(item.get("storage_key") or ""), content_sha256=str(item.get("content_sha256") or ""))
            item.update(delivered.fields())
            item.pop("delivery_error", None)
            if source_path is not None and (before_sha256 != delivered.content_sha256 or image_content(read_image_file(source_path))[0] != delivered.content_sha256):
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间源图片内容已变化，请重新准备")
        if profile:
            try:
                current = self.current_profile()
                if current["id"] != profile["id"] or config_version(current) != config_version(profile):
                    raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间托管配置已变化，请重新准备")
            except ImageHostingError:
                raise ImageHostingError("IMAGE_DELIVERY_STALE", "上传期间托管配置已变化，请重新准备") from None
        prepared.setdefault("source", {})["image_pool"] = pool
        return prepared


__all__ = ["ImageDeliveryService", "ImageDeliveryProblem", "ImageHttpsProvider", "build_image_https_provider"]

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.parse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Protocol

from erp_web.context import AppPaths
from erp_web.product_model import draft_image_asset_ids, normalize_image_pool


IMAGE_HTTPS_PROVIDER_ENV = "ERP_IMAGE_HTTPS_PROVIDER"
IMAGE_HTTPS_BASE_URL_ENV = "ERP_IMAGE_HTTPS_BASE_URL"
IMAGE_HTTPS_ROOT_ENV = "ERP_IMAGE_HTTPS_ROOT"


class ImageDeliveryError(RuntimeError):
    """图片无法被当前 HTTPS provider 物化时的稳定领域错误。"""


@dataclass(frozen=True)
class ImageDeliverySettings:
    provider: str
    public_base_url: str
    public_root: Path

    @classmethod
    def from_environment(cls, paths: AppPaths) -> "ImageDeliverySettings":
        provider = str(os.environ.get(IMAGE_HTTPS_PROVIDER_ENV) or "existing_url").strip().lower()
        base_url = str(os.environ.get(IMAGE_HTTPS_BASE_URL_ENV) or "").strip().rstrip("/")
        raw_root = str(os.environ.get(IMAGE_HTTPS_ROOT_ENV) or "").strip()
        public_root = Path(raw_root).expanduser() if raw_root else paths.images_dir / "public"
        if not public_root.is_absolute():
            public_root = paths.app_dir / public_root
        return cls(
            provider=provider,
            public_base_url=base_url,
            public_root=public_root.resolve(),
        )


@dataclass(frozen=True)
class DeliveredImage:
    storage_key: str
    public_url: str
    content_sha256: str
    provider: str


@dataclass(frozen=True)
class ImageDeliveryProblem:
    asset_id: str
    code: str
    message: str
    next_action: str


class ImageHttpsProvider(Protocol):
    """将图片物化为稳定 storage key 与公网 HTTPS URL 的 provider 契约。"""

    name: str

    def deliver(
        self,
        *,
        source_path: Path | None,
        storage_key: str,
        content_sha256: str,
    ) -> DeliveredImage:
        ...


class ExistingUrlOnlyProvider:
    """默认 provider：保留已有公网 URL，不处理本地文件。"""

    name = "existing_url"

    def deliver(
        self,
        *,
        source_path: Path | None,
        storage_key: str,
        content_sha256: str,
    ) -> DeliveredImage:
        raise ImageDeliveryError(
            "本地图片尚未配置 HTTPS provider；请设置 "
            f"{IMAGE_HTTPS_PROVIDER_ENV}=local_static 和 {IMAGE_HTTPS_BASE_URL_ENV}"
        )


class LocalStaticHttpsProvider:
    """把本地图片复制到独立静态目录，再通过外部 HTTPS 入口暴露。"""

    name = "local_static"
    _IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

    def __init__(self, settings: ImageDeliverySettings) -> None:
        if not settings.public_base_url.startswith("https://"):
            raise ImageDeliveryError(
                f"{IMAGE_HTTPS_BASE_URL_ENV} 必须是 https:// 开头的公网地址"
            )
        self.public_base_url = settings.public_base_url
        self.public_root = settings.public_root.resolve()

    @staticmethod
    def _safe_storage_key(value: str) -> str:
        key = str(value or "").strip().replace("\\", "/").lstrip("/")
        parts = PurePosixPath(key).parts
        if not key or any(part in {"", ".", ".."} for part in parts):
            raise ImageDeliveryError("图片 storage_key 非法")
        return PurePosixPath(*parts).as_posix()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def public_url(self, storage_key: str) -> str:
        return f"{self.public_base_url}/{urllib.parse.quote(storage_key, safe='/')}"

    def stored_path(self, storage_key: str) -> Path:
        key = self._safe_storage_key(storage_key)
        destination = (self.public_root / key).resolve()
        try:
            destination.relative_to(self.public_root)
        except ValueError as exc:
            raise ImageDeliveryError("图片 storage_key 超出公开目录") from exc
        return destination

    def deliver(
        self,
        *,
        source_path: Path | None,
        storage_key: str,
        content_sha256: str,
    ) -> DeliveredImage:
        source = source_path if source_path and source_path.is_file() else None
        digest = str(content_sha256 or "").strip().lower()
        key = str(storage_key or "").strip()

        if source is not None:
            digest = self._sha256(source)
            suffix = source.suffix.lower()
            if suffix not in self._IMAGE_SUFFIXES:
                raise ImageDeliveryError(f"不支持发布该图片格式：{suffix or '无扩展名'}")
            key = f"assets/{digest[:2]}/{digest}{suffix}"
        elif not key:
            raise ImageDeliveryError("图片本地文件不存在，且没有可复用的 storage_key")

        key = self._safe_storage_key(key)
        destination = self.stored_path(key)

        if source is not None and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            try:
                shutil.copyfile(source, temporary_path)
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
        if not destination.is_file():
            raise ImageDeliveryError(f"公开图片文件不存在：{key}")

        return DeliveredImage(
            storage_key=key,
            public_url=self.public_url(key),
            content_sha256=digest,
            provider=self.name,
        )


ProviderFactory = Callable[[ImageDeliverySettings], ImageHttpsProvider]


_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    ExistingUrlOnlyProvider.name: lambda settings: ExistingUrlOnlyProvider(),
    LocalStaticHttpsProvider.name: LocalStaticHttpsProvider,
}


def build_image_https_provider(settings: ImageDeliverySettings) -> ImageHttpsProvider:
    factory = _PROVIDER_FACTORIES.get(settings.provider)
    if factory is None:
        supported = "、".join(sorted(_PROVIDER_FACTORIES))
        raise ImageDeliveryError(
            f"未知图片 HTTPS provider：{settings.provider}；可用值：{supported}"
        )
    return factory(settings)


SettingsProvider = Callable[[AppPaths], ImageDeliverySettings]


class ImageDeliveryService:
    """发布前图片解析边界；平台代码不接触隧道、目录或对象存储细节。"""

    def __init__(
        self,
        paths: AppPaths,
        settings_provider: SettingsProvider = ImageDeliverySettings.from_environment,
    ) -> None:
        self.paths = paths
        self.settings_provider = settings_provider

    def _source_path(self, item: dict[str, object]) -> Path | None:
        for key in ("path", "preview_url", "url"):
            value = str(item.get(key) or "").strip()
            if not value:
                continue
            if value.startswith("/file?"):
                parsed = urllib.parse.urlparse(value)
                value = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            elif value.startswith("file:"):
                value = urllib.parse.urlparse(value).path
            elif value.startswith(("http://", "https://", "ml-id:")):
                continue
            candidate = Path(value).expanduser()
            if not candidate.is_absolute():
                candidate = self.paths.app_dir / candidate
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _target_asset_ids(
        product: dict[str, object],
        pool: list[dict[str, object]],
        platform: str,
    ) -> set[str]:
        drafts = product.get("drafts") if isinstance(product.get("drafts"), dict) else {}
        draft = drafts.get(platform) if isinstance(drafts.get(platform), dict) else {}
        draft_ids = set(draft_image_asset_ids(draft.get("images")))
        if draft_ids:
            return draft_ids

        candidates = [
            item
            for item in pool
            if not item.get("platforms")
            or platform
            in {str(value or "").strip().lower() for value in item.get("platforms") or []}
        ]
        selected = [item for item in candidates if bool(item.get("selected"))]
        return {
            str(item.get("id") or "").strip()
            for item in (selected or candidates)
            if str(item.get("id") or "").strip()
        }

    def inspect_product(
        self, product: dict[str, object], platform: str,
    ) -> list[ImageDeliveryProblem]:
        """只读检查所选图片的当前交付状态；不复制文件、不写数据、不探测网络。"""
        source = product.get("source") if isinstance(product.get("source"), dict) else {}
        pool = normalize_image_pool(source.get("image_pool") or [], "source")
        target_ids = self._target_asset_ids(product, pool, platform)
        settings = self.settings_provider(self.paths)
        problems: list[ImageDeliveryProblem] = []
        prepare_action = "调用 product_publish_prepare 准备当前草稿的发布图片，再重新校验"
        for item in pool:
            asset_id = str(item.get("id") or "")
            if asset_id not in target_ids:
                continue
            url = str(item.get("url") or "").strip()
            key = str(item.get("storage_key") or "")
            managed = bool(key or item.get("delivery_provider"))
            if url.startswith(("https://", "http://")) and not managed:
                continue
            code, message, action = "", "", prepare_action
            if url.startswith(("data:", "blob:", "ml-id:")) and not self._source_path(item):
                code, message = "IMAGE_NOT_PUBLIC", "图片地址不能直接用于该平台发布"
                action = "将图片导入本地图片池，再调用 product_publish_prepare"
            else:
                try:
                    provider = build_image_https_provider(settings)
                    if isinstance(provider, ExistingUrlOnlyProvider):
                        raise ImageDeliveryError(
                            "本地图片尚未配置 HTTPS provider；请设置 "
                            f"{IMAGE_HTTPS_PROVIDER_ENV}=local_static 和 {IMAGE_HTTPS_BASE_URL_ENV}"
                        )
                except ImageDeliveryError as exc:
                    code, message = "IMAGE_PROVIDER_NOT_CONFIGURED", str(exc)
                    action = "修正图片 HTTPS provider 配置后调用 product_publish_prepare"
                else:
                    stored = None
                    try:
                        if isinstance(provider, LocalStaticHttpsProvider) and key:
                            stored = provider.stored_path(key)
                    except ImageDeliveryError as exc:
                        code, message = "IMAGE_STORAGE_INVALID", str(exc)
                    if not code:
                        if self._source_path(item) is None and not (stored and stored.is_file()):
                            code, message = "IMAGE_SOURCE_MISSING", "本地源图片和可复用的公开图片文件均不存在"
                            action = "重新导入此图片后调用 product_publish_prepare"
                        elif not key or not stored or not stored.is_file() or not url:
                            code, message = "IMAGE_NOT_PREPARED", "图片 HTTPS provider 已配置，但此图片尚未完成发布准备"
                        elif item.get("delivery_provider") != settings.provider or url != provider.public_url(key):
                            code, message = "IMAGE_PUBLIC_URL_STALE", "图片托管地址与当前 HTTPS provider 配置不一致，需要刷新"
                        if item.get("delivery_error") and code == "IMAGE_NOT_PREPARED":
                            code, message = "IMAGE_PREPARATION_FAILED", f"上次图片准备失败：{item['delivery_error']}"
            if code:
                problems.append(ImageDeliveryProblem(asset_id, code, message, action))
        for asset_id in sorted(target_ids - {str(item.get("id") or "") for item in pool}):
            problems.append(ImageDeliveryProblem(asset_id, "IMAGE_SOURCE_MISSING", "草稿引用的图片已不在图片池中", "从图片池重新选择图片"))
        return problems

    def prepare_product(
        self,
        product: dict[str, object],
        platform: str,
    ) -> dict[str, object]:
        prepared = deepcopy(product)
        platform = str(platform or "").strip().lower()
        source = prepared.get("source") if isinstance(prepared.get("source"), dict) else {}
        pool = normalize_image_pool(
            source.get("image_pool") if isinstance(source.get("image_pool"), list) else [],
            "source",
        )
        target_ids = self._target_asset_ids(prepared, pool, platform)
        settings = self.settings_provider(self.paths)
        try:
            provider = build_image_https_provider(settings)
            provider_error = ""
        except ImageDeliveryError as exc:
            provider = None
            provider_error = str(exc)

        for item in pool:
            asset_id = str(item.get("id") or "").strip()
            if asset_id not in target_ids:
                continue
            public_url = str(item.get("url") or "").strip()
            managed = bool(item.get("storage_key") or item.get("delivery_provider"))
            if public_url.startswith(("https://", "http://")) and not managed:
                item.pop("delivery_error", None)
                continue

            # 已托管 URL 是可重新计算的缓存；切换 provider/base URL 时不能复用旧地址。
            if managed:
                item["url"] = ""
            try:
                if provider is None:
                    raise ImageDeliveryError(provider_error)
                delivered = provider.deliver(
                    source_path=self._source_path(item),
                    storage_key=str(item.get("storage_key") or ""),
                    content_sha256=str(item.get("content_sha256") or ""),
                )
                item["storage_key"] = delivered.storage_key
                item["content_sha256"] = delivered.content_sha256
                item["delivery_provider"] = delivered.provider
                item["url"] = delivered.public_url
                item.pop("delivery_error", None)
            except (ImageDeliveryError, OSError) as exc:
                item["delivery_error"] = str(exc)

        source["image_pool"] = pool
        prepared["source"] = source
        return prepared


__all__ = [
    "DeliveredImage",
    "ExistingUrlOnlyProvider",
    "IMAGE_HTTPS_BASE_URL_ENV",
    "IMAGE_HTTPS_PROVIDER_ENV",
    "IMAGE_HTTPS_ROOT_ENV",
    "ImageDeliveryError",
    "ImageDeliveryProblem",
    "ImageDeliveryService",
    "ImageDeliverySettings",
    "ImageHttpsProvider",
    "LocalStaticHttpsProvider",
    "build_image_https_provider",
]

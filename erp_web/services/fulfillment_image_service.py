"""平台商品图原样托管后交付仓库，避免第三方图片的跨站防盗链。"""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request

from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestNotSent
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_content import MAX_IMAGE_BYTES, image_content
from erp_web.services.image_hosting_config import default_profile
from erp_web.services.image_hosting_transport import safe_image_urlopen
from erp_web.services.s3_image_storage import S3ImageStorage


def deliver_platform_image(config, source_url):
    """只改变交付地址，源图内容不变；失败发生在报单写请求之前。"""
    stage = "平台商品图片读取"
    try:
        def read(url, *, referer=""):
            req = Request(url, headers={"Accept": "image/*", **({"Referer": referer} if referer else {})})
            ctx = request_context(url, method="GET", source=__name__, platform="image_hosting:public", semantics="read", timeout=30)
            ctx = replace(ctx, credential_id="public")
            with managed_urlopen(req, timeout=30, request_context=ctx, transport=safe_image_urlopen, source=__name__) as response:
                return response.read(MAX_IMAGE_BYTES + 1)

        data = read(source_url)
        digest, suffix, _ = image_content(data)
        stage = "平台商品图片托管"
        # 复用现有内容寻址对象和 S3 SDK；临时文件退出后删除，不混用商品本地图。
        with TemporaryDirectory(prefix="fulfillment-image-") as directory:
            path = Path(directory) / f"{digest}.{suffix}"
            path.write_bytes(data)
            delivered = S3ImageStorage(default_profile(config)).deliver(source_path=path)
        stage = "仓库商品图片跨站读取核验"
        public_data = read(delivered.public_url, referer="https://www.kuajing84.com/")
        if image_content(public_data)[0] != digest:
            raise FulfillmentError("托管后的商品图片与平台原图不一致，已停止报单。")
        return delivered.public_url
    except FulfillmentError:
        raise
    except HTTPError as exc:
        raise FulfillmentError(f"{stage}失败（HTTP {exc.code}），已停止报单，请检查图片访问及托管配置。") from None
    except ImageHostingError as exc:
        raise FulfillmentError(f"{stage}失败：{exc}") from None
    except (ExternalRequestNotSent, ExternalRequestBlocked) as exc:
        raise FulfillmentError(f"{stage}失败：{exc}") from None
    except Exception:
        raise FulfillmentError(f"{stage}失败，已停止报单，请检查图片访问及托管配置。") from None

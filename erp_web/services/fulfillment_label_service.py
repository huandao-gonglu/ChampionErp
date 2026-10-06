"""面单文件交付：限量 PDF，经现有 S3 边界上传，再核验公开可读取。"""

import base64
import hashlib
import logging
import re
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request

from erp_web.schemas.external_requests import ExternalRequestBlocked, ExternalRequestOutcomeUnknown
from erp_web.schemas.fulfillment import FulfillmentError
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.external_request_context import request_context
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.services.image_hosting_config import default_profile
from erp_web.services.image_hosting_transport import safe_image_urlopen
from erp_web.services.s3_image_storage import S3ImageStorage

MAX_LABEL_BYTES = 8 * 1024 * 1024
logger = logging.getLogger(__name__)


def label_pdf(content_base64):
    try:
        data = base64.b64decode(content_base64, validate=True)
    except (ValueError, TypeError):
        raise FulfillmentError("面单文件编码无效，请重新选择 PDF。") from None
    return validate_label_pdf(data)


def validate_label_pdf(data: bytes) -> bytes:
    if not data or len(data) > MAX_LABEL_BYTES:
        raise FulfillmentError("面单 PDF 不能超过 8 MB。")
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
        raise FulfillmentError("文件不是完整 PDF，请重新导出平台面单。")
    return data


def deliver_label(config, order_id, content_base64):
    return deliver_label_bytes(config, order_id, label_pdf(content_base64))


def deliver_label_bytes(config, order_id, data: bytes):
    data = validate_label_pdf(data)
    stage = "默认 S3 托管配置检查"
    try:
        profile = default_profile(config)
        # 相同订单、相同内容复用对象；键中不含订单号及收件人信息。
        scope = hashlib.sha256(order_id.encode()).hexdigest()
        digest = hashlib.sha256(data).hexdigest()
        stage = "S3 面单上传及对象校验"
        storage = S3ImageStorage(profile)
        url = storage.deliver_pdf(data=data, scope=scope)
        stage = "面单公开下载核验"
        request = Request(url, headers={"Accept": "application/pdf"})
        ctx = request_context(url, method="GET", source=__name__, platform="image_hosting:public", account_id=profile["id"], semantics="read", timeout=30)
        # 不让请求审计记录含收件人信息的面单响应正文。
        ctx = replace(ctx, credential_id="public")
        with managed_urlopen(request, timeout=30, request_context=ctx, transport=safe_image_urlopen, source=__name__) as response:
            public_data = response.read()
        if hashlib.sha256(public_data).hexdigest() != digest:
            raise FulfillmentError("面单上传后公开读取的内容不一致，请检查托管配置。")
        return {"ok": True, "url": url}
    except FulfillmentError:
        raise
    except (ImageHostingError, ExternalRequestBlocked) as exc:
        # 这些边界错误均由项目生成并已脱敏，保留阶段与可操作原因。
        raise FulfillmentError(f"{stage}失败：{exc}") from None
    except ExternalRequestOutcomeUnknown:
        raise FulfillmentError("S3 面单上传回执尚未确认，请重新获取时先核验原对象；不要修改面单文件后反复上传。") from None
    except HTTPError as exc:
        advice = "请检查桶公开读取权限和公开地址。" if stage == "面单公开下载核验" else "请检查 S3 端点、桶及对象读写权限。"
        raise FulfillmentError(f"{stage}失败（HTTP {exc.code}），{advice}") from None
    except Exception as exc:
        code = str(getattr(exc, "code", ""))
        logger.warning("面单交付失败：阶段=%s，类型=%s，错误码=%s", stage, type(exc).__name__, code if re.fullmatch(r"[A-Z0-9_]{1,100}", code) else "")
        raise FulfillmentError(f"{stage}失败，请检查该阶段的托管配置、权限和网络。") from None

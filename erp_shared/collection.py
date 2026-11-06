"""采集页面身份和验证状态的共享规则。"""
import re
from urllib.parse import urlsplit

VERIFICATION_REASONS = frozenset({"LOGIN", "CAPTCHA", "SLIDER", "SECURITY", "ROBOT"})
WAIT_MESSAGE = "等待人工验证：请在采集浏览器的原商品页完成登录或验证码，完成后自动继续。"


def same_collection_page(expected_url: str, current_url: str) -> bool:
    expected, current = urlsplit(expected_url), urlsplit(current_url)
    if expected.scheme not in {"http", "https"} or not expected.hostname:
        return False
    if expected._replace(fragment="") == current._replace(fragment=""):
        return True
    # 1688 验证后的追踪参数不影响商品身份；其他 URL 的查询参数可能是商品 ID。
    return bool(
        expected.hostname == current.hostname == "detail.1688.com"
        and expected.path == current.path
        and re.fullmatch(r"/offer/\d+\.html", expected.path)
    )



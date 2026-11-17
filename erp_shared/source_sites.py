# -*- coding: utf-8 -*-
from __future__ import annotations

"""采集源注册表。

新增采集源只需要增加一个 ``SourceSiteSpec``，工作流不再维护解析、登录页、
验证码、错误码和浏览器 profile 的平行分支。
"""

from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from erp_shared.collection import same_collection_page
from erp_shared.html import html_to_text
from erp_shared.source_parser import has_1688_signed_in_buyer

VERIFY_MARKERS = (
    "安全验证",
    "slide.1688.com",
    "请验证身份",
    "验证码",
    "captcha",
    "verify",
    "security verification",
)

AMAZON_VERIFY_MARKERS = (
    "robot check",
    "captcha",
    "enter the characters you see below",
    "validatecaptcha",
    "sorry, this page is not available",
    "this item is no longer available",
)

Parser = Callable[[str | dict[str, Any], str], dict[str, Any]]
PageCheck = Callable[[str, str, str, str], bool]


def _contains(values: tuple[str, ...], *parts: str) -> bool:
    text = "\n".join(str(part or "") for part in parts).casefold()
    return any(value.casefold() in text for value in values)


def _never(_url: str, _html: str, _text: str, _title: str) -> bool:
    return False


def _login_1688(url: str, html: str, text: str, title: str) -> bool:
    # 商品页导航中的“登录”入口和脚本不能让人工验证永远无法结束。
    return (urlsplit(url).hostname in {"login.1688.com", "login.taobao.com", "passport.alibaba.com"}
            or _contains(("登录",), title)
            or _contains(("帐号密码登录", "账号密码登录", "短信登录", "请登录后继续"), text or html_to_text(html)))


def _captcha_1688(url: str, html: str, text: str, title: str) -> bool:
    return _contains(tuple(VERIFY_MARKERS) + ("滑块", "安全验证", "/_____tmd_____/punish"), url, title, text or html_to_text(html))


def _login_amazon(url: str, _html: str, text: str, title: str) -> bool:
    return "signin" in str(url or "").casefold() or "sign in" in str(title).casefold()


def _captcha_amazon(url: str, html: str, text: str, title: str) -> bool:
    return _contains(("robot check", "captcha", "enter the characters you see below", "validatecaptcha"), url, title, text or html_to_text(html))


def _region_amazon(_url: str, html: str, text: str, _title: str) -> bool:
    return _contains(
        (
            "cannot be shipped to your selected location",
            "not deliverable",
            "currently unavailable",
            "this item cannot be shipped",
            "not available in your region",
        ),
        html,
        text,
    )


@dataclass(frozen=True)
class SourceSiteSpec:
    key: str
    name: str
    host_markers: tuple[str, ...]
    browser_profile: str
    error_codes: dict[str, str]
    login_check: PageCheck = _never
    captcha_check: PageCheck = _never
    region_check: PageCheck = _never
    supports_api_collect: bool = False
    playwright_fallback: bool = False
    required_quality_fields: tuple[str, ...] = ("title", "images")

    def matches(self, value: str) -> bool:
        text = str(value or "").casefold()
        return any(marker.casefold() in text for marker in self.host_markers)

    def error_code(self, reason: str = "") -> str:
        reason = str(reason or "").strip().upper()
        return self.error_codes.get(reason, self.error_codes.get("DEFAULT", "COLLECT_FAILED"))

    def diagnose(
        self,
        url: str,
        html: str,
        text: str,
        title: str,
        *,
        detect_slider: bool = False,
        expected_product_url: str = "",
    ) -> tuple[dict[str, bool], str]:
        login = self.login_check(url, html, text, title)
        captcha = self.captcha_check(url, html, text, title)
        region = self.region_check(url, html, text, title)
        reason = ""
        if self.key == "1688":
            if login:
                reason = "LOGIN"
            elif detect_slider and _contains(("滑块", "slider"), text):
                reason = "SLIDER"
            elif captcha:
                reason = "CAPTCHA"
            elif same_collection_page(expected_product_url, url) and not has_1688_signed_in_buyer(html):
                login = True
                reason = "LOGIN"
        elif captcha:
            reason = "ROBOT"
        elif login:
            reason = "LOGIN"
        elif region:
            reason = "REGION"
        return {
            "is_login_page": login,
            "is_captcha_page": captcha,
            "is_security_check_page": captcha,
        }, reason

    def quality_reason(self, flags: dict[str, Any], current: str = "") -> str:
        if current:
            return current
        checks = {
            "title": ("title_found", "NO_TITLE"),
            "images": ("images_found_count", "NO_IMAGES"),
            "dimensions": ("dimensions_found", "NO_DIMENSIONS"),
            "weight": ("weight_found", "NO_WEIGHT"),
        }
        for field in self.required_quality_fields:
            flag, reason = checks[field]
            value = flags.get(flag)
            if not bool(value):
                return reason
        return ""

    def next_action(self, error_code: str) -> str:
        code = str(error_code or "").upper()
        if not code:
            return "采集已完成，可进入采集箱继续 AI 文案、生图和编辑。"
        if self.key == "1688":
            if "API" in code:
                return "请检查 1688 官方 API 凭证、接口权限和商品详情接口地址；未开通权限时可切回浏览器采集。"
            if any(key in code for key in ("LOGIN", "CAPTCHA", "SECURITY", "SLIDER", "REMOTE_DEBUGGING")):
                return "请在采集浏览器的原商品页完成登录或验证码，再继续采集。"
            return "请确认商品链接可访问，使用浏览器会话重新采集；缺失资料可在采集箱中补齐。"
        if self.key == "amazon":
            if any(key in code for key in ("ROBOT", "REGION", "LOGIN", "FORBIDDEN")):
                return "请在采集浏览器完成登录并确认地区设置，再重试当前商品链接。"
            return "请确认商品链接和浏览器登录状态后重新采集；缺失资料可在采集箱中补齐。"
        return "请确认商品链接可访问后重新采集；来源资料不完整时可在采集箱中补齐。"


SOURCE_SITES: tuple[SourceSiteSpec, ...] = (
    SourceSiteSpec(
        key="1688",
        name="1688",
        host_markers=("1688.com",),
        browser_profile="1688",
        error_codes={
            "LOGIN": "1688_LOGIN_REQUIRED",
            "SECURITY": "1688_SECURITY_CHECK",
            "CAPTCHA": "1688_CAPTCHA_REQUIRED",
            "SLIDER": "1688_SLIDER_REQUIRED",
            "NO_IMAGES": "1688_IMAGE_NOT_FOUND",
            "NO_TITLE": "1688_TITLE_NOT_FOUND",
            "SELECTOR": "1688_SELECTOR_FAILED",
            "PROFILE": "1688_BROWSER_PROFILE_NOT_FOUND",
            "REMOTE": "1688_REMOTE_DEBUGGING_NOT_CONNECTED",
            "NETWORK": "NETWORK_BLOCKED",
            "API": "1688_API_FAILED",
            "DEFAULT": "1688_SELECTOR_FAILED",
        },
        login_check=_login_1688,
        captcha_check=_captcha_1688,
        supports_api_collect=True,
        playwright_fallback=True,
        # 包装资料属于核价/发布条件；来源未提供时仍允许采集入库。
        required_quality_fields=("title", "images"),
    ),
    SourceSiteSpec(
        key="amazon",
        name="Amazon",
        host_markers=("amazon.",),
        browser_profile="amazon",
        error_codes={
            "ROBOT": "AMAZON_ROBOT_CHECK",
            "REGION": "AMAZON_REGION_BLOCKED",
            "NO_IMAGES": "AMAZON_IMAGE_NOT_FOUND",
            "NO_TITLE": "AMAZON_TITLE_NOT_FOUND",
            "NO_DIMENSIONS": "AMAZON_DIMENSIONS_NOT_FOUND",
            "NO_WEIGHT": "AMAZON_WEIGHT_NOT_FOUND",
            "SELECTOR": "AMAZON_SELECTOR_FAILED",
            "LOGIN": "AMAZON_LOGIN_REQUIRED",
            "NETWORK": "NETWORK_BLOCKED",
            "FORBIDDEN": "HTTP_FORBIDDEN",
            "DEFAULT": "AMAZON_SELECTOR_FAILED",
        },
        login_check=_login_amazon,
        captcha_check=_captcha_amazon,
        region_check=_region_amazon,
        required_quality_fields=("title", "images", "dimensions", "weight"),
    ),
    SourceSiteSpec(
        key="generic",
        name="通用网页",
        host_markers=(),
        browser_profile="collect",
        error_codes={"DEFAULT": "COLLECT_FAILED", "NETWORK": "NETWORK_BLOCKED"},
        required_quality_fields=("title", "images"),
    ),
)

_SOURCE_SITES_BY_KEY = {site.key: site for site in SOURCE_SITES}
GENERIC_SOURCE_SITE = _SOURCE_SITES_BY_KEY["generic"]


def detect_source_site(value: str) -> str:
    for site in SOURCE_SITES:
        if site.host_markers and site.matches(value):
            return site.key
    return GENERIC_SOURCE_SITE.key


def source_site(platform: str) -> SourceSiteSpec:
    return _SOURCE_SITES_BY_KEY.get(str(platform or "").strip().lower(), GENERIC_SOURCE_SITE)


__all__ = [
    "GENERIC_SOURCE_SITE",
    "SOURCE_SITES",
    "SourceSiteSpec",
    "detect_source_site",
    "source_site",
]

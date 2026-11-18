"""采集清单写入前解析 1688 分享链接，保留用户选择的采集方式。"""
from collections.abc import Callable
from html import unescape
from http.client import HTTPException
import re
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import Request

from pydantic import ValidationError

from erp_web.schemas.collection import CollectionLinkInput, CollectionLinksAddResult
from erp_web.schemas.external_requests import ExternalRequestBlocked
from erp_web.schemas.requests import RequestValidationError
from erp_web.services.external_request_manager import managed_urlopen
from erp_web.stores.collection_links import CollectionLinks


_HTTP_PREFIX = re.compile(r'https?://', re.IGNORECASE)
_HTTP_URL = re.compile(r'https?://[^\s<>"\'【】。，；、（）]+', re.IGNORECASE)
_1688_URL = re.compile(r'https?://(?:[a-z0-9-]+\.)*1688\.com(?=[:/?#]|$)', re.IGNORECASE)
_TARGET_URL = re.compile(r'(?:https?|wireless1688)://[^\s<>"\']+', re.IGNORECASE)
_MAX_RESPONSE_BYTES = 1024 * 1024


def _input_links(value: str, mode: str) -> list[str]:
    text = re.sub(r'(https?)\\:', r'\1:', value.strip(), flags=re.IGNORECASE)
    parts = text.split()
    if parts and all(_HTTP_PREFIX.match(part) for part in parts):
        return parts
    urls = [match.group().rstrip(')]') for match in _HTTP_URL.finditer(text)]
    if any(_1688_URL.match(url) for url in urls):
        return urls
    if mode != 'api':
        return parts or ['']
    if not urls:
        raise RequestValidationError('未找到 1688 商品链接，请粘贴完整分享文案或商品网址，不能只填写口令。')
    return urls


def _short_link_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in ('http', 'https') and parsed.hostname == 'qr.1688.com'
                and parsed.username is None and parsed.password is None and parsed.port in (None, 80, 443)
                and re.fullmatch(r'/s/[a-zA-Z0-9]{1,128}', parsed.path)):
            return 'https://qr.1688.com' + parsed.path
    except ValueError:
        pass
    raise RequestValidationError('不支持该 1688 分享地址，请复制 qr.1688.com/s/ 开头的短链接或标准商品链接。')


def _target_offer_id(url: str) -> str:
    """只读取商品目标地址，不从分享口令、标题或任意数字中猜商品编号。"""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 80, 443):
            return ''
    except ValueError:
        return ''
    if parsed.scheme in ('http', 'https') and (host == '1688.com' or host.endswith('.1688.com')):
        match = re.fullmatch(r'/offer/([0-9]+)\.html', parsed.path)
        if match:
            return match.group(1)
    if parsed.scheme not in ('http', 'https', 'wireless1688') or host != 'ma.m.1688.com' or parsed.path != '/offer':
        return ''
    query = parse_qs(parsed.query)
    ids = {
        match.group(1)
        for value in query.get('offerId', []) + query.get('id', [])
        if (match := re.fullmatch(r'([0-9]+)(?:\.html(?:\?.*)?)?', value))
    }
    return ids.pop() if len(ids) == 1 else ''


def _resolve_short_link(url: str, deadline: float) -> str:
    for _ in range(3):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RequestValidationError('1688 分享链接解析超时，请稍后重试。')
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html,*/*'})
        try:
            with managed_urlopen(request, timeout=min(10, remaining), follow_redirects=False, source=__name__) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise RequestValidationError('1688 分享链接返回内容过大，请使用标准商品链接。')
            targets = _TARGET_URL.findall(unescape(raw.decode('utf-8', errors='replace')))
        except HTTPError as exc:
            location = exc.headers.get('Location', '') if exc.headers else ''
            exc.close()
            if exc.code not in (301, 302, 303, 307, 308) or not location:
                raise RequestValidationError('1688 分享链接读取失败，请检查链接是否有效后重试。') from None
            try:
                target = urljoin(url, unescape(location))
            except ValueError:
                raise RequestValidationError('1688 分享链接跳转地址无效，请使用标准商品链接。') from None
            offer_id = _target_offer_id(target)
            if offer_id:
                return f'https://detail.1688.com/offer/{offer_id}.html'
            # 不自动访问重定向目标；仅允许继续读取同一固定短链域名。
            url = _short_link_url(target)
            continue
        except ExternalRequestBlocked as exc:
            raise RequestValidationError(str(exc), status_code=409, error_code=exc.code) from None
        except (OSError, HTTPException):
            raise RequestValidationError('1688 分享链接读取超时或网络异常，请稍后重试。') from None
        offer_ids = {_target_offer_id(target) for target in targets} - {''}
        if len(offer_ids) == 1:
            return f'https://detail.1688.com/offer/{offer_ids.pop()}.html'
        raise RequestValidationError('未能从 1688 分享链接确定商品，请在手机阿里中重新分享，或使用标准商品链接。')
    raise RequestValidationError('1688 分享链接跳转次数过多，请使用标准商品链接。')


def _resolve_new_collection_links(entries: list) -> list[dict[str, str]]:
    """先校验整批待添加链接，再解析并返回可保存的 URL。"""
    items = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('url'), str):
            items.append(CollectionLinkInput.model_validate(entry))
            continue
        for url in _input_links(entry['url'], entry.get('mode', 'browser')):
            index = len(items) + 1
            if not _HTTP_PREFIX.match(url):
                raise RequestValidationError(f'第 {index} 个链接无效：请输入以 http:// 或 https:// 开头的完整商品链接。')
            try:
                items.append(CollectionLinkInput.model_validate({**entry, 'url': url}))
            except ValidationError as exc:
                detail = exc.errors(include_input=False)[0]
                reason = (detail.get('ctx') or {}).get('error') or '请检查商品链接和采集方式。'
                raise RequestValidationError(f'第 {index} 个链接无效：{reason}') from None
    cache = {}
    deadline = monotonic() + 60
    for item in items:
        if urlsplit(item.url).hostname == 'qr.1688.com':
            short_url = _short_link_url(item.url)
            if short_url not in cache:
                cache[short_url] = _resolve_short_link(short_url, deadline)
            item.url = cache[short_url]
    return [item.model_dump() for item in items]


def add_collection_links(
    entries: list,
    *,
    store: CollectionLinks,
    before_save: Callable[[], object] | None = None,
) -> CollectionLinksAddResult:
    items = _resolve_new_collection_links(entries)
    if before_save is not None:
        before_save()
    counts = store.add(items)
    return CollectionLinksAddResult(**counts, urls=tuple(dict.fromkeys(item['url'] for item in items)))

"""Canvas 后端图片适配器；本机实现位于 desktop_runtime。"""
import base64
from pathlib import Path
from .ai_provider_contracts import AiImageEditProvider, CAPABILITY_IMAGE_EDIT
from .desktop_connection_service import (
    DesktopUnavailable,
    call_desktop,
    current_desktop_connection,
    delegate_connection_id,
    delegate_desktop_connection,
)
from . import image_service


from erp_shared.browser_ai import CANVAS_PREVIEW_WAIT_SECONDS, is_canvas_model


def _with_managed_canvas_url(model):
    if not isinstance(model, dict) or not model.get('canvas_managed'):
        return model
    from erp_web.context import peek_context
    ctx = peek_context()
    url_getter = getattr(getattr(ctx, 'config', None), 'gemini_canvas_url', None) if ctx is not None else None
    if not callable(url_getter):
        return model
    return {**model, 'canvas_url': url_getter()}


def check_canvas_connection(app_dir, model, timeout=30, *, force_login=False):
    model = _with_managed_canvas_url(model)
    try:
        return call_desktop('canvas.check', {'model': model, 'force_login': force_login, 'timeout_seconds': timeout},
                            timeout=timeout + 300 + CANVAS_PREVIEW_WAIT_SECONDS)
    except DesktopUnavailable as exc:
        if exc.outcome_unknown:
            raise DesktopUnavailable("未收到桌面端的 Canvas 连接检查结果，请确认桌面连接后重试。") from exc
        raise


def list_canvas_accounts(*, timeout=10):
    result = call_desktop('canvas.accounts', {}, timeout=timeout)
    accounts = result.get('accounts') if isinstance(result, dict) else []
    return accounts if isinstance(accounts, list) else []


def canvas_accounts_status():
    """读取本机或同账号唯一在线桌面的只读 Google 账号摘要；绝不读取凭据或阻断配置页加载。"""
    from erp_web.context import peek_context
    if peek_context() is None:
        return {'scope': 'web_offline', 'desktop_online': False, 'accounts': []}
    if current_desktop_connection():
        try:
            return {'scope': 'desktop', 'desktop_online': True, 'accounts': list_canvas_accounts(timeout=5)}
        except DesktopUnavailable:
            return {'scope': 'desktop', 'desktop_online': False, 'accounts': []}
    if delegate_connection_id():
        try:
            with delegate_desktop_connection():
                return {'scope': 'web_delegated', 'desktop_online': True, 'accounts': list_canvas_accounts(timeout=5)}
        except DesktopUnavailable:
            return {'scope': 'web_offline', 'desktop_online': False, 'accounts': []}
    return {'scope': 'web_offline', 'desktop_online': False, 'accounts': []}


def login_canvas_account(app_dir, model, *, profile='', force_login=False, check_only=False, timeout=45):
    del app_dir
    model = _with_managed_canvas_url(model)
    wait_seconds = timeout + CANVAS_PREVIEW_WAIT_SECONDS + (0 if check_only else 300)
    try:
        return call_desktop(
            'canvas.account.login',
            {
                'model': model,
                'profile': str(profile or '').strip(),
                'force_login': bool(force_login),
                'check_only': bool(check_only),
                'timeout_seconds': int(timeout),
            },
            timeout=wait_seconds,
        )
    except DesktopUnavailable as exc:
        if exc.outcome_unknown:
            raise DesktopUnavailable('未收到桌面端的 Google 账号登录结果，请确认桌面连接后重试。') from exc
        raise


def logout_canvas_account(profile: str):
    return call_desktop('canvas.account.logout', {'profile': str(profile or '').strip()}, timeout=30)


def toggle_canvas_account(profile: str, enabled: bool):
    return call_desktop('canvas.account.toggle', {'profile': str(profile or '').strip(), 'enabled': bool(enabled)}, timeout=15)


class GeminiCanvasProvider(AiImageEditProvider):
    provider_id = 'gemini_canvas'

    def supports(self, model, capability):
        return capability == CAPABILITY_IMAGE_EDIT and is_canvas_model(model)

    def edit_images(self, request):
        images = []
        for item in request.images:
            path = next((path for key in ('path', 'local_path')
                         if (path := image_service.resolve_local_path(str(item.get(key) or ''), request.app_dir)) and path.is_file()), None)
            if path is None or path.stat().st_size > 20 * 1024 * 1024:
                raise ValueError('Canvas 源图片不存在或超过 20 MB。')
            images.append({'id': str(item.get('id') or ''), 'data': base64.b64encode(path.read_bytes()).decode('ascii')})
        result = call_desktop('canvas.edit', {'model': _with_managed_canvas_url(request.model), 'prompt': request.prompt,
            'images': images, 'mode': request.mode, 'count': request.count,
            'combine_references': request.combine_references, 'timeout_seconds': int(request.timeout_seconds)}, timeout=request.timeout_seconds + 300 + CANVAS_PREVIEW_WAIT_SECONDS)
        from io import BytesIO
        from PIL import Image
        outputs = []
        for item in result.get('images', []):
            encoded = item.get('data') or ''
            if len(encoded) > 28 * 1024 * 1024:
                raise ValueError('Canvas 返回图片超过 20 MB。')
            raw = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(raw)) as image:
                mime = Image.MIME.get(image.format, '')
                image.verify()
            suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}.get(mime)
            if not suffix or item.get('mime') != mime:
                raise ValueError('Canvas 返回图片格式无效。')
            outputs.append({**{k: v for k, v in item.items() if k != 'data'}, 'bytes': raw, 'suffix': suffix})
        if not outputs:
            raise RuntimeError('Canvas 没有返回图片。')
        return outputs


__all__ = [
    'GeminiCanvasProvider',
    'canvas_accounts_status',
    'check_canvas_connection',
    'is_canvas_model',
    'list_canvas_accounts',
    'login_canvas_account',
    'logout_canvas_account',
    'toggle_canvas_account',
]

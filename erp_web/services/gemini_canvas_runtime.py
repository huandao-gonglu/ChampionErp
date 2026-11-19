"""Canvas 后端图片适配器；本机实现位于 desktop_runtime。"""
import base64
from pathlib import Path
from .ai_provider_contracts import AiImageEditProvider, CAPABILITY_IMAGE_EDIT
from .desktop_connection_service import DesktopUnavailable, call_desktop
from . import image_service


from erp_shared.browser_ai import CANVAS_PREVIEW_WAIT_SECONDS, is_canvas_model


def check_canvas_connection(app_dir, model, timeout=30, *, force_login=False):
    try:
        return call_desktop('canvas.check', {'model': model, 'force_login': force_login, 'timeout_seconds': timeout},
                            timeout=timeout + 300 + CANVAS_PREVIEW_WAIT_SECONDS)
    except DesktopUnavailable as exc:
        if exc.outcome_unknown:
            raise DesktopUnavailable("未收到桌面端的 Canvas 连接检查结果，请确认桌面连接后重试。") from exc
        raise


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
        result = call_desktop('canvas.edit', {'model': request.model, 'prompt': request.prompt,
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


__all__ = ['GeminiCanvasProvider', 'check_canvas_connection', 'is_canvas_model']

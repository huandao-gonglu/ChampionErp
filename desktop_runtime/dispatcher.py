"""固定动作表；不提供任意命令、脚本或业务数据库入口。"""
from dataclasses import asdict
import base64
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit

from erp_shared.desktop import LocalImageEditRequest, validate_action
from . import browser_ai_runtime, collection
from .browser_debug_service import open_auth_link_in_browser
from .local_actions import open_profile
from .cli import _cli_command_parts, _run_codex_cli_text
from .gemini_canvas_runtime import GeminiCanvasProvider, check_canvas_connection
from .operation import remaining_seconds


def dispatch(config, operation, params):
    params = validate_action(operation, params)
    timeout = max(1, int(remaining_seconds(3600)))
    if operation == 'config.read':
        return {'models': config.read()}
    if operation == 'config.write':
        return config.update(params['models'])
    if operation == 'browser.status':
        return collection.browser_status(config)
    if operation == 'browser.open':
        return collection.open_browser(config, params['url'])
    if operation == 'browser.profile':
        return open_profile(config)
    if operation == 'collect.read':
        return collection.read_collection(config, **params)
    if operation == 'collect.verify':
        return collection.inspect_verification(config, **params)
    if operation == 'auth.open':
        if urlsplit(params['url']).scheme not in {'http', 'https'}:
            raise ValueError('授权链接必须使用 HTTP 或 HTTPS。')
        return open_auth_link_in_browser(**params)
    model = config.model(params['model'])
    timeout = min(timeout, params.get('timeout_seconds') or timeout)
    if operation == 'cli.status':
        executable = _cli_command_parts(str(model.get('command') or 'codex'))[0]
        return {'command': str(model.get('command') or 'codex'), 'command_path': shutil.which(executable) or ''}
    if operation == 'cli.run':
        text = _run_codex_cli_text(config.directory, model, params['prompt'], timeout)
        if params['probe_image']:
            text = _inline_cli_probe_image(text, config.directory)
        return {'text': text}
    if operation == 'browser.ai.open':
        return asdict(browser_ai_runtime.open_browser_ai_page(config.directory, model, timeout))
    if operation == 'browser.ai.chat':
        return asdict(browser_ai_runtime.run_browser_ai_chat(config.directory, model, params['prompt'], timeout=timeout))
    if operation == 'canvas.check':
        check_canvas_connection(config.directory, model, timeout, force_login=params['force_login'])
        return {'ok': True, 'profile_dir': str(browser_ai_runtime.browser_ai_profile_dir(config.directory, model)),
                'browser_url': browser_ai_runtime.browser_ai_url(model), 'port': browser_ai_runtime.browser_ai_port(model)}
    if operation == 'canvas.edit':
        with tempfile.TemporaryDirectory(prefix='champion-canvas-') as directory:
            images = []
            for index, item in enumerate(params['images']):
                encoded = item.get('data')
                if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
                    raise ValueError('源图片无效或超过 20 MB。')
                path = Path(directory) / f'{index}.image'
                path.write_bytes(base64.b64decode(encoded, validate=True))
                images.append({'id': str(item.get('id') or ''), 'path': str(path)})
            results = GeminiCanvasProvider().edit_images(LocalImageEditRequest(
                app_dir=config.directory, model=model, prompt=params['prompt'], images=images,
                mode=params['mode'], count=params['count'], timeout_seconds=timeout,
                combine_references=params['combine_references'],
            ))
            return {'images': [{**{k: v for k, v in item.items() if k != 'bytes'},
                                'data': base64.b64encode(item['bytes']).decode('ascii')} for item in results]}
    raise ValueError('不支持的本机操作。')


def _inline_cli_probe_image(text, directory):
    """CLI 生成文件只在本机检查，后端探测不读取服务器上的同名路径。"""
    from erp_shared.ai_parsing import parse_json_text
    from urllib.parse import urlsplit, unquote
    import json
    from PIL import Image
    try:
        data = parse_json_text(text)
    except ValueError:
        # 沿用原 CLI 探测支持的 file:// 回执。
        import re
        match = re.search(r"file://[^\s)>\]\"']+", text)
        if not match:
            return text
        data = {'can_generate_image': True, 'image_path': match.group(0)}
    value = data.get('image_path') or data.get('path') or data.get('local_path')
    if not value:
        return text
    path = Path(unquote(urlsplit(value).path)) if str(value).startswith('file://') else Path(value)
    if not path.is_absolute():
        path = directory / path
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('CLI 未返回可读取的图片，或图片超过 20 MB。')
    with Image.open(path) as image:
        image.verify()
    for key in ('image_path', 'path', 'local_path'):
        data.pop(key, None)
    data['image_base64'] = base64.b64encode(path.read_bytes()).decode('ascii')
    return json.dumps(data, ensure_ascii=False)

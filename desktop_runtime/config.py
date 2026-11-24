"""本机连接配置的唯一 owner；资料目录与旧桌面数据相邻，迁移不复制登录资料。"""
import json
from pathlib import Path
from threading import RLock
from erp_shared.browser_ai import normalize_canvas_url, resolve_local_settings
from erp_shared.desktop import LOCAL_MODEL_FIELDS, local_configuration_fingerprint


class LocalConfig:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / 'config' / 'desktop-connections.json'
        self.lock = RLock()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.browser_port = 19222
        self.browser_profile = directory / 'browser_profile' / '1688'
        self.debug_dir = directory / 'data' / 'cache' / 'collect_debug'

    def read(self):
        with self.lock:
            if not self.path.exists():
                return {}
            value = json.loads(self.path.read_text())
            if not isinstance(value, dict):
                raise ValueError('本机连接配置必须是 JSON 对象。')
            return value

    def update(self, models):
        with self.lock:
            value = self.read()
            for key, model in models.items():
                if not key or not isinstance(model, dict) or set(model) - LOCAL_MODEL_FIELDS or any(not isinstance(v, str) for v in model.values()):
                    raise ValueError('本机连接配置包含未知字段。')
                value[key] = {**value.get(key, {}), **model}
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
            temporary.chmod(0o600)
            temporary.replace(self.path)
            return {'models': value, 'fingerprints': {key: local_configuration_fingerprint(model) for key, model in value.items()}}

    def model(self, model):
        # 后端只提供业务模型和连接引用；本机配置不能由推理请求覆盖。
        reference = str(model.get('local_connection_id') or model.get('id') or '')
        business_fields = {'id', 'name', 'connection_type', 'provider', 'browser_provider', 'cli_tool',
                           'model', 'timeout_seconds', 'capabilities', 'capability_profiles',
                           'local_connection_id', 'local_config_fingerprint', 'enabled',
                           'canvas_managed', 'canvas_url'}
        business = {k: v for k, v in model.items() if k in business_fields}
        saved = self.read().get(reference)
        local = resolve_local_settings(business, saved)
        expected = model.get('local_config_fingerprint')
        if expected and expected != local_configuration_fingerprint(local):
            if saved is None:
                raise ValueError('当前桌面尚未配置此模型的本机连接，请在系统设置中配置、测试并保存模型。')
            raise ValueError('本机连接配置已变化，请在当前桌面重新测试并保存模型。')
        result = {**business, **local}
        if business.get('canvas_managed') and business.get('canvas_url'):
            # 内置 Gemini Canvas 项目地址由管理员统一配置，桌面端严格校验域名白名单后生效。
            result['browser_url'] = normalize_canvas_url(business['canvas_url'])
        return result

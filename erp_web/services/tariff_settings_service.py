"""共享 FBS 运费版本、个人覆盖和显式升级确认。"""
import base64
import binascii
from contextlib import contextmanager
from datetime import date
import fcntl
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from erp_web.international_shipping.tariff_store import atomic_write, import_template, load_active, read_current
from erp_web.schemas.managed_settings import TariffImportRequest, TariffUpgradeRequest, TariffRulesQuery, TariffRulesResponse
from erp_web.schemas.requests import RequestValidationError


class TariffSettingsService:
    def __init__(self, personal_dir: Path, shared_dir: Path):
        self.personal_dir = personal_dir
        self.shared_dir = shared_dir

    @contextmanager
    def _publication_scope(self):
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        with (self.shared_dir / '.settings.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def load(self, platform, version=None):
        """新核价选择当前有效来源，单批由 ShippingModule 固定快照。"""
        return load_active(self._directory(platform), platform, version)

    def _directory(self, platform, scope='effective'):
        if scope == 'effective' and platform in read_current(self.personal_dir)['platforms']:
            return self.personal_dir
        return self.shared_dir

    def rules(self, query: TariffRulesQuery) -> TariffRulesResponse:
        directory = self._directory(query.platform, query.scope)
        pack = load_active(directory, query.platform)
        content = pack['content']
        rules = []
        for row in content['rules']:
            rule = {key: str(row.get(key) or '') for key in (
                'id', 'name', 'carrier', 'service', 'transport', 'group', 'fixed', 'per_g',
                'step_g', 'min_g', 'max_g', 'max_edge_cm', 'max_sum_cm', 'volume_divisor_g', 'source_cell',
            )}
            currency = 'CNY' if query.platform == 'ozon' else 'RUB'
            rule.update(currency=currency, price_currency=currency, price_min=str(row.get('price_min_cny') or ''),
                        price_max=str(row.get('price_max_cny') or ''), battery=row.get('battery'), liquid=row.get('liquid'))
            if query.platform == 'ozon':
                rule['step_g'] = '1'
            elif row['group'] == 'Extra Small':
                policy = content['policies']['extra_small']
                rule.update(max_g=str(policy['max_g']), price_max=str(policy['price_max_rub']),
                            max_edge_cm=str(policy['max_edge_cm']), max_sum_cm=str(policy['max_sum_cm']))
            rules.append(rule)
        warnings = list(content.get('warnings', []))
        if query.platform == 'yandex' and any(row['group'] == 'Other' for row in rules):
            warnings.append('普通件模板缺少完整准入限制，其运费结果用于试算。')
        return {'ok': True, 'platform': query.platform, 'source': 'personal' if directory == self.personal_dir else 'shared',
                'version': pack['version'], 'filename': Path(str(pack.get('source_path') or '').replace('\\', '/')).name,
                'sheet': str(content.get('sheet') or ''), 'imported_at': pack.get('imported_at', ''),
                'effective_date': content.get('effective_date') or '', 'warnings': warnings, 'rules': rules,
                'calculation_notes': ['fixed 为每个包裹固定处理费，不是首重费；per_g 为每克费率，step_g 为重量向上进位步长。',
                    'max_g、max_edge_cm、max_sum_cm、price_max 为空表示表内未注明上限，不表示无限制。',
                    'volume_divisor_g 为空时按实际重量计费；非空时按本渠道实重与体积重取大。',
                    'battery/liquid 为 null 表示准入未知，不能解释为允许。',
                    'Yandex Extra Small 的 price_max 用核价最终舍入后的买家售价（RUB）校验，不是采购成本或自行申报金额。']}


    @staticmethod
    def _summary(directory, platform):
        try:
            pack = load_active(directory, platform)
            return {'version': pack['version'], 'effective_date': pack['content'].get('effective_date') or '',
                    'rules_count': len(pack['content']['rules']), 'error': ''}
        except FileNotFoundError:
            return {'version': '', 'effective_date': '', 'rules_count': 0, 'error': '运费版本文件缺失，请重新导入'}
        except ValueError as exc:
            error = '尚未配置运费表，请由管理员发布或导入个人运费表' if '尚未导入费率' in str(exc) else str(exc)
            return {'version': '', 'effective_date': '', 'rules_count': 0, 'error': error}

    def detail(self):
        index = read_current(self.personal_dir)
        items = []
        for platform in ('yandex', 'ozon'):
            shared = self._summary(self.shared_dir, platform)
            customized = platform in index['platforms']
            active = self._summary(self.personal_dir, platform) if customized else shared
            base = index.get('customized_from', {}).get(platform, '')
            items.append({'platform': platform, 'source': 'personal' if customized else 'shared',
                          **active, 'shared': shared,
                          'update_available': customized and bool(shared['version']) and shared['version'] != base})
        return {'ok': True, 'items': items}

    def versions(self):
        return {item['platform']: item['version'] or item['error'] for item in self.detail()['items']}

    def import_file(self, body, *, is_admin=False):
        request = TariffImportRequest.model_validate(body)
        if request.scope == 'shared' and not is_admin:
            raise RequestValidationError('发布共享运费表需要管理员权限。', status_code=403)
        if request.effective_date and request.effective_date > date.today():
            raise ValueError('本次导入的生效日期尚未到，请在生效后发布')
        try:
            data = base64.b64decode(request.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('文件编码无效，请重新选择运费表') from None
        if len(data) > 8 * 1024 * 1024:
            raise ValueError('运费表不能超过 8 MiB')
        directory = self.shared_dir if request.scope == 'shared' else self.personal_dir
        with self._publication_scope(), TemporaryDirectory(prefix='erp-tariff-') as temp:
            shared = self._summary(self.shared_dir, request.platform)
            current = self._summary(directory, request.platform)
            if request.scope == 'personal' and request.platform not in read_current(directory)['platforms']:
                current = shared
            if request.expected_version != current['version']:
                raise RequestValidationError('运费表已更新，请重新读取并预览。', status_code=409)
            path = Path(temp) / request.filename
            path.write_bytes(data)
            result = import_template(
                path, request.platform, directory,
                request.effective_date.isoformat() if request.effective_date else None, request.preview,
                expected_version=read_current(directory)['platforms'].get(request.platform, ''),
                customized_from=shared['version'] if request.scope == 'personal' else None,
            )
            # 文件路径只供服务端存档，不随接口泄露服务器目录。
            result.pop('report', None)
            return {'ok': True, 'preview': request.preview, 'diff': result, **self.detail()}

    def upgrade(self, body):
        request = TariffUpgradeRequest.model_validate(body)
        with self._publication_scope():
            shared = load_active(self.shared_dir, request.platform)
            self.personal_dir.mkdir(parents=True, exist_ok=True)
            with (self.personal_dir / '.import.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                index = read_current(self.personal_dir)
                if (shared['version'] != request.shared_version
                        or index['platforms'].get(request.platform) != request.expected_version):
                    raise RequestValidationError('运费表已更新，请重新读取后确认。', status_code=409)
                index['platforms'].pop(request.platform)
                index.get('customized_from', {}).pop(request.platform, None)
                atomic_write(self.personal_dir / 'current.json', json.dumps(index, ensure_ascii=False, indent=2).encode())
            return self.detail()

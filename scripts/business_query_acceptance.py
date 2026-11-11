"""业务查询真实问答验收：从普通页面建立事实基准，独立检查模型答案及原生用量。"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time
from uuid import uuid4

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    fields: str


CASES = (
    Case('forwarders', '查看已保存报单货代配置，区分启用、停用、默认标记和默认规则；不要把可选目录当成已配置。',
         'enabled_address_ids:启用收货点ID数组；disabled_address_ids:停用ID数组；enabled_carriers:启用承运商代码数组；default_address_ids:启用且默认的ID数组；fulfillment_rule_count:默认报单规则数量；no_forwarder_configured:是否完全没有启用的承接配置；realtime_verified:是否实际核验远端'),
    Case('warehouse_roles', '仅查询当前保存的配置：报单收货地址是否参与核价候选筛选？列出当前配置中可参与比价的全部 Yandex 发布仓，不查询商品、草稿、订单或历史执行记录。',
         'local_receiving_filters_pricing:本地报单地址是否参与核价候选筛选；configured_pricing_warehouse_ids:当前配置中可参与比价的全部发布仓ID数组；realtime_verified:是否实际核验远端'),
    Case('system', '查询订单自动同步间隔和图片水印样式，只读取已有设置。',
         'orders_auto_sync_interval_hours:同步间隔小时；watermark:包含text、opacity、size_percent、density、color的对象'),
    Case('hosting', '查询图片托管默认目标和保存的配置状态，不执行上传或联网测试。',
         'default_profile_id:默认配置ID；profile_ids:配置ID数组；realtime_verified:是否本次实际验证了公网连通'),
    Case('models', '查询已配置模型的启用状态和各 AI 功能绑定，不检查本机连接或调用模型测试。',
         'enabled_model_ids:启用模型ID数组；bindings:功能ID到非空model_id字符串的对象，未显式绑定的功能省略'),
    Case('research', '查询选品搜索的默认市场、搜索模式及启用的数据源，不执行选品搜索。',
         'target_markets:默认市场ID数组；search_mode:默认搜索模式；enabled_provider_ids:启用的数据源ID数组'),
)


NATURAL_CASES = (
    Case('forwarders', '查看我当前配置的报单货代。分别列出启用的收货点、默认地址标记、对应仓库、承接物流商和默认报单规则。区分实际配置与可选目录；只读取现有配置，不刷新、不修改、不报单。', ''),
    Case('configuration_details', '我给 UNI 和 GUOO(GTA) 分别配置了哪个收货点和承接规则？合作渠道和仓库有哪些？如果默认报单规则为空，能否说明我没有配置货代？请核对现有数据，只读查询，不刷新或修改。', ''),
    Case('rules_and_forwarders', 'Yandex 轻小件物流的尺寸、重量和售价上限是多少？这些限制与我配置的报单货代收货仓分别起什么作用？请查实际配置和运费规则解释，只读现有资料，不重新核价或刷新远端。', ''),
)


def expected_facts(pages):
    logistics, fulfillment = pages['logistics.settings'], pages['fulfillment.settings']
    addresses = logistics['addresses']
    enabled = [row for row in addresses if row['enabled']]
    models, research = pages['settings.models'], pages['settings.product_research']
    return {
        'forwarders': {
            'enabled_address_ids': [row['id'] for row in enabled],
            'disabled_address_ids': [row['id'] for row in addresses if not row['enabled']],
            'enabled_carriers': sorted({rule['carrier'] for row in enabled for rule in row['rules']}),
            'default_address_ids': [row['id'] for row in enabled if row['is_default']],
            'fulfillment_rule_count': len(fulfillment['rules']),
            'no_forwarder_configured': not (any(row['rules'] for row in enabled) or any(rule.get('confirmed') for rule in fulfillment['rules'])), 'realtime_verified': False,
        },
        'warehouse_roles': {'local_receiving_filters_pricing': False,
            'configured_pricing_warehouse_ids': sorted({str(row['publish_warehouse_id']) for row in pages['yandex.warehouses']['pricing_options']}),
            'realtime_verified': False},
        'system': {'orders_auto_sync_interval_hours': pages['settings.system']['orders_auto_sync_interval_hours'],
            'watermark': {key: pages['settings.system']['watermark'][key] for key in ('text', 'opacity', 'size_percent', 'density', 'color')}},
        'hosting': {'default_profile_id': pages['settings.image_hosting']['image_hosting']['default_profile_id'],
            'profile_ids': [row['id'] for row in pages['settings.image_hosting']['image_hosting']['profiles']], 'realtime_verified': False},
        'models': {'enabled_model_ids': [row['id'] for row in models['models'] if row.get('enabled', True)],
            'bindings': {key: value.get('model_id', '') for key, value in pages['settings.model_bindings'].items() if value.get('model_id')}},
        'research': {'target_markets': research['search_defaults']['target_markets'],
            'search_mode': research['search_defaults']['search_mode'],
            'enabled_provider_ids': [row['id'] for row in research['search_providers'] if row.get('enabled', True)]},
    }


def parse_answer(text):
    # 允许简短开场或 Markdown 围栏；不通过重新调用模型修复错误答案。
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char == '{':
            try:
                value, _ = decoder.raw_decode(text[index:])
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    raise ValueError('模型没有返回可核对的 JSON 对象')


def compare_answer(answer, expected):
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value
    errors = []
    for key, value in expected.items():
        actual = answer.get(key)
        if key == 'configured_pricing_warehouse_ids' and isinstance(actual, list):
            actual = [str(item) for item in actual]
        if key not in answer or type(actual) is not type(value) or canonical(actual) != canonical(value):
            errors.append({'field': key, 'expected': value, 'actual': actual})
    return errors


def context_metrics(history):
    """实际 usage 与可重现的消息体量分开计量，不虚构分项精确 token。"""
    inputs, outputs, tool_sizes, calls, nested_calls = [], [], [], [], []
    parts_chars = Counter()
    instructions = set()
    errors = []
    for message in history:
        if message.get('instructions'):
            instructions.add(message['instructions'])
        usage = message.get('usage') or {}
        if usage:
            inputs.append(usage.get('input_tokens', 0)); outputs.append(usage.get('output_tokens', 0))
        for part in message.get('parts', []):
            kind = part.get('part_kind', 'unknown')
            metadata = part.get('metadata') or {}
            if metadata.get('code_mode'):
                for nested in metadata.get('tool_calls', {}).values():
                    if isinstance(nested, dict):
                        nested_calls.append({'name': nested.get('tool_name'), 'args': nested.get('args')})
            parts_chars[kind] += len(json.dumps(part, ensure_ascii=False, separators=(',', ':')))
            if kind == 'tool-call':
                calls.append({'name': part['tool_name'], 'args': part.get('args')})
            if kind == 'tool-return':
                tool_sizes.append(len(json.dumps(part.get('content'), ensure_ascii=False, separators=(',', ':'))))
                if isinstance(part.get('content'), dict) and isinstance(part['content'].get('error'), dict):
                    errors.append(part['content']['error'].get('code'))
    return {'peak_input_tokens': max(inputs, default=0), 'sum_input_tokens': sum(inputs),
            'sum_output_tokens': sum(outputs), 'model_requests': len(inputs),
            'instruction_chars': max(map(len, instructions), default=0), 'message_part_chars': dict(parts_chars),
            'max_tool_result_chars': max(tool_sizes, default=0), 'tool_calls': calls, 'all_tool_calls': [*calls,*nested_calls], 'business_errors': errors}


def unexpected_business_reads(calls):
    """配置题不扫描商品、草稿和历史订单，避免答案虽对却无限扩展取数。"""
    allowed = ('settings.', 'shipping.', 'yandex.', 'logistics.', 'fulfillment.', 'pricing.')
    unexpected = set()
    for call in calls:
        if call.get('name') != 'business_data_read':
            continue
        args = call.get('args') or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                continue
        if not isinstance(args, dict):
            continue
        resource = args.get('resource', '')
        if resource and not resource.startswith(allowed):
            unexpected.add(resource)
    return sorted(unexpected)


def run_live(args):
    folder = args.output.resolve(); folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    ssh = ['ssh', '-p', str(args.ssh_port), '-i', str(args.ssh_identity.expanduser()), '-o', 'BatchMode=yes',
           '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', args.ssh_host]
    def remote(code):
        command = 'cd /opt/champion-erp/current && runuser -u champion-erp -- env ERP_APP_DIR=/var/lib/champion-erp .venv/bin/python -c ' + shlex.quote(code)
        return json.loads(subprocess.check_output([*ssh, command], text=True, timeout=45))
    def digest():
        return remote("""import json,sqlite3,hashlib
from pathlib import Path
root=Path('/var/lib/champion-erp')
con=sqlite3.connect('file:'+str(root/'data/order-notifications.sqlite3')+'?mode=ro',uri=True)
data={table:con.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in ('logistics_addresses','fulfillment_rules','bus_settings')}
files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'config').glob('*.json')}
print(json.dumps({'business':hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'configuration':files}))""")
    credentials = remote("""import json
from pathlib import Path
from erp_web.stores.account_store import AccountStore
store=AccountStore(Path('/var/lib/champion-erp/accounts.sqlite3')); admin=store.admin()
assert admin and admin['status']=='enabled'
token,session=store.create_session(admin['id'],admin['password_hash'])
print(json.dumps({'token':token,'csrf':session.csrf_token,'hash':session.token_hash}))""")
    client = requests.Session(); client.cookies.set('champion_session', credentials['token'])
    client.headers.update({'X-CSRF-Token': credentials['csrf'], 'Origin': args.url, 'User-Agent': 'gongluErp-business-acceptance'})
    def read(path, body=None):
        response = client.get(args.url + path, timeout=45) if body is None else client.post(args.url + path, json=body, timeout=45)
        response.raise_for_status(); return response.json()
    report = {'cases': [], 'page_parity': [], 'skipped_large_fields': [], 'bounded_samples': [], 'business_unchanged': False}
    try:
        before = digest()
        report['instruction_sections_chars'] = remote("""import json
from pathlib import Path
from erp_web.services.global_agent_chat_service import GlobalAgentChatService
service=GlobalAgentChatService.__new__(GlobalAgentChatService); service.app_dir=Path('/var/lib/champion-erp')
print(json.dumps({key:len(value) for key,value in service.instruction_sections().items()}))""")
        from erp_web.business_query_composition import BUSINESS_QUERIES
        paths = {query.name: query.path for query in BUSINESS_QUERIES.definitions}
        needed = ('logistics.settings', 'fulfillment.settings', 'yandex.warehouses', 'settings.system',
                  'settings.image_hosting', 'settings.models', 'settings.model_bindings', 'settings.product_research')
        pages = {name: read(paths[name]) for name in needed}
        # 覆盖所有不带必填参数的普通查询，比较完整页结果与按字段读取。
        directory = read('/api/business-data/catalog?limit=50')
        for resource in directory['resources']:
            name = resource['name']
            if any(hint.get('required') for hint in resource.get('parameter_hints', {}).values()):
                continue
            if name not in paths:
                raise AssertionError('线上资源在本地验收版本中不存在：'+name)
            if not name.startswith(('settings.', 'shipping.', 'yandex.', 'logistics.', 'fulfillment.', 'pricing.')):
                sample=read('/api/business-data/read', {'resource':name,'limit':2})
                report['bounded_samples'].append({'resource':name,'total':sample['total'],
                    'returned':len(sample['data']) if isinstance(sample['data'],list) else int(sample['data'] is not None),
                    'limited':sample['limited']})
                print('有界读取检查：'+name,flush=True)
                continue
            print('正在核对配置：'+name,flush=True)
            page = pages.get(name) if name in pages else read(paths[name])
            def verify(path, value):
                offset = 0
                if isinstance(value, str) and len(value) > 8000:
                    part = read('/api/business-data/read', {'resource': name, 'path': path})
                    assert part['total'] == len(value) and value.startswith(part['data']), (name, path)
                    report['skipped_large_fields'].append(name+'.'+path)
                    return
                if isinstance(value, list):
                    while True:
                        chunk = read('/api/business-data/read', {'resource': name, 'path': path, 'offset': offset, 'limit': 50})
                        assert chunk['total'] == len(value), (name, path)
                        if chunk['limited'] and not chunk['data']:
                            verify(('' if path == '$' else path)+'/'+str(offset), value[offset])
                            offset += 1
                            if offset == len(value): break
                            continue
                        assert chunk['data'] == value[offset:offset+len(chunk['data'])], (name,path)
                        if chunk['next_offset'] is None: break
                        assert chunk['next_offset'] > offset, (name,path)
                        offset = chunk['next_offset']
                else:
                    chunk = read('/api/business-data/read', {'resource': name, 'path': path})
                    if chunk['limited'] and isinstance(value, dict):
                        for key, child in value.items():
                            verify(('' if path == '$' else path)+'/'+key.replace('~', '~0').replace('/', '~1'), child)
                    else:
                        assert not chunk['limited'] and chunk['data'] == value, (name,path)
            verify('$', page)
            report['page_parity'].append(name)
            print('配置同源通过：'+name,flush=True)
        expected=expected_facts(pages)
        for case in (NATURAL_CASES if args.natural else CASES):
            if args.case and case.name not in args.case: continue
            conversation='conversation_global_chat_'+uuid4().hex
            prompt=case.question+' 全程只读本地资料，不刷新、不修改、不联网测试。仅返回一个 JSON 对象，不使用表格，字段：'+case.fields+'。数组必须完整、不能重复；没有配置时用空数组或空字符串，不凭枚举推断已配置。'
            if args.natural: prompt=case.question
            body={'trigger':'submit-message','id':conversation,'messages':[{'id':'acceptance-'+uuid4().hex,'role':'user','parts':[{'type':'text','text':prompt}]}]}
            started=time.monotonic(); chunks=[]; stream_errors=[]
            with client.post(args.url+'/api/v1/ai-chat/runs',json=body,stream=True,timeout=(30,300)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith(b'data:'):continue
                    raw=line[5:].strip()
                    if raw==b'[DONE]':continue
                    event=json.loads(raw)
                    if event.get('type')=='text-delta':chunks.append(event.get('delta',''))
                    if event.get('type')=='error':stream_errors.append(event)
            history=read('/api/v1/ai-work/conversations/'+conversation)['messages']
            try:
                answer=None if args.natural else parse_answer(''.join(chunks))
                errors=[] if args.natural else compare_answer(answer,expected[case.name])
            except ValueError as exc:
                answer=None;errors=[{'format':str(exc)}]
            metrics=context_metrics(history)
            forbidden=[c['name'] for c in metrics['tool_calls'] if c['name'] not in {'business_data_read','business_data_catalog','run_code','search_tools'}]
            item={'case':case.name,'question':prompt,'conversation_id':conversation,'seconds':round(time.monotonic()-started,2),
                  'answer':answer,'answer_text':''.join(chunks),'expected':expected.get(case.name),'errors':errors,'stream_errors':stream_errors,
                  'unexpected_tools':forbidden,'unexpected_resources':unexpected_business_reads(metrics['all_tool_calls']), **metrics}
            item['completed_without_error']=bool(chunks) and not (stream_errors or forbidden or item['unexpected_resources'])
            item['passed']=None if args.natural else item['completed_without_error'] and not errors
            report['cases'].append(item)
            (folder/(case.name+'.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2))
            print(json.dumps({key:item[key] for key in ('case','conversation_id','passed','peak_input_tokens','model_requests','instruction_chars','seconds','errors')},ensure_ascii=False),flush=True)
        report['business_unchanged']=before==digest()
        report['case_selection']=args.case or []
        report['requires_manual_review']=args.natural
        report['completed_without_error']=bool(report['cases']) and report['business_unchanged'] and all(case['completed_without_error'] for case in report['cases'])
        report['passed']=None if args.natural else report['completed_without_error'] and all(case['passed'] for case in report['cases'])
        (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if (report['completed_without_error'] if args.natural else report['passed']) else 1
    finally:
        try:read('/api/account/logout',{})
        finally:
            remote("from pathlib import Path; import json; from erp_web.stores.account_store import AccountStore; AccountStore(Path('/var/lib/champion-erp/accounts.sqlite3')).revoke_session("+repr(credentials['hash'])+"); print(json.dumps({'revoked':True}))")
        client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='显式执行生产配置的真实模型问答')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--natural',action='store_true',help='执行原始自然语言题并保留待人工复核结果，不自动判定准确性通过')
    parser.add_argument('--url',default='https://hdgl.cc.cd')
    parser.add_argument('--ssh-host',default='root@107.174.48.241')
    parser.add_argument('--ssh-port',type=int,default=22)
    parser.add_argument('--ssh-identity',type=Path,default=Path.home()/'.ssh/id_ed25519')
    parser.add_argument('--case',action='append',choices=sorted({case.name for case in (*CASES,*NATURAL_CASES)}))
    args=parser.parse_args()
    if not args.live:
        from erp_web.ai_capability_coverage import AI_CAPABILITY_COVERAGE_MANIFEST
        from erp_web.business_query_inventory import audit_query_coverage
        from erp_web.business_query_composition import BUSINESS_QUERIES
        rows=audit_query_coverage({e.path for e in AI_CAPABILITY_COVERAGE_MANIFEST if e.method=='GET' and e.path.startswith('/api/')},BUSINESS_QUERIES)
        print(json.dumps(rows,ensure_ascii=False,indent=2));return 0
    if args.output is None:parser.error('--live 必须指定仓库外的 --output 目录')
    if args.output.resolve().is_relative_to(ROOT):parser.error('临时业务验收结果必须写到仓库外')
    args.url=args.url.rstrip('/')
    return run_live(args)


if __name__=='__main__':
    raise SystemExit(main())

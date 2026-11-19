"""统一后端与本机执行器的边界验收；不连接真实浏览器、模型或店铺。"""
from concurrent.futures import ThreadPoolExecutor
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
import time
import urllib.request

from PIL import Image
import pytest
from pydantic_ai import CancellationToken, RunCancelled

from erp_web.context import get_context
from erp_web.http_handler import Handler
from http.server import ThreadingHTTPServer
from tests.account_test_utils import authenticated_headers
from erp_web.services.desktop_connection_service import DesktopConnections, DesktopUnavailable, bind_desktop_connection
from erp_web.services.ai_run_cancellation import bind_cancellation_token

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://detail.1688.com/offer/123.html'


def png():
    output = BytesIO()
    Image.new('RGB', (64, 64), 'blue').save(output, 'PNG')
    return output.getvalue()


def test_runtime_import_and_pipe_start_without_business_database(tmp_path):
    code = "import desktop_runtime.dispatcher,sys; assert not any(x.startswith('erp_web') for x in sys.modules)"
    subprocess.run([sys.executable, '-c', code], cwd=ROOT, check=True)
    process = subprocess.Popen([sys.executable, '-m', 'desktop_runtime', '--data-dir', str(tmp_path / 'runtime')],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(process.stdout.readline()) == {'ready': True, 'pid': process.pid}
        process.stdin.write(json.dumps({'id': 'status', 'operation': 'config.read', 'params': {}, 'timeout': 5}) + '\n')
        process.stdin.flush()
        reply = json.loads(process.stdout.readline())
        assert reply == {'id': 'status', 'ok': True, 'result': {'models': {}}}
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        assert not list((tmp_path / 'runtime').rglob('*.sqlite3'))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_backend_does_not_import_local_execution_implementations(tmp_path):
    code = "import erp_web.server,sys; assert not any(x.startswith('desktop_runtime') for x in sys.modules)"
    subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                   env={**os.environ, 'ERP_APP_DIR': str(tmp_path)}, check=True)


def test_full_collection_over_http_shares_backend_product_and_asset():
    context = get_context()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    auth_headers = authenticated_headers(server)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def post(path, body, connection=''):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', **auth_headers, **({'X-Champion-Desktop': connection} if connection else {})})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def desktop(connection):
        calls = post('/api/desktop/poll', {}, connection)['calls']
        assert len(calls) == 1 and calls[0]['operation'] == 'collect.read'
        call = calls[0]
        assert call['params']['product_url'] == URL
        post('/api/desktop/asset', {'id': call['id'], 'asset': {
            'url': 'https://example.test/product.png', 'data': base64.b64encode(png()).decode(), 'mime': 'image/png',
        }}, connection)
        post('/api/desktop/result', {'id': call['id'], 'ok': True, 'result': {
            'snapshot': {'url': URL, 'title': '采集测试商品', 'html': '<title>采集测试商品</title><script>{"buyerModel":{"userId":42}}</script><img src="https://example.test/product.png">',
                         'text': '采集测试商品', 'image_urls': ['https://example.test/product.png'], 'browser_tab_id': 'original'},
            'browserStatus': {'connected': True},
        }}, connection)

    try:
        connection = post('/api/desktop/connect', {})['connection_id']
        def availability(headers):
            with urllib.request.urlopen(urllib.request.Request(base + '/api/operation-availability', headers=headers)) as response:
                return json.load(response)
        assert availability(auth_headers)['desktop_reason']
        assert not availability({**auth_headers, 'X-Champion-Desktop': connection})['desktop_reason']
        browser_request = urllib.request.Request(base + '/api/desktop/connect', data=b'{}',
            headers={**auth_headers, 'Content-Type': 'application/json', 'Origin': base, 'Sec-Fetch-Mode': 'cors'})
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(browser_request)
        assert denied.value.code == 403 and json.load(denied.value)['error_code'] == 'DESKTOP_PROTOCOL_FORBIDDEN'
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(desktop, connection)
            result = post('/api/collect-resume', {'source_url': URL, 'browser_tab_id': 'original'}, connection)
            worker.result(timeout=10)
        assert result['ok'], result
        saved = result['product']
        with urllib.request.urlopen(urllib.request.Request(base + '/api/products-index', headers=auth_headers)) as response:
            items = json.load(response)['items']
        assert saved['product_id'] in {row.get('product_id') or row.get('id') for row in items}
        asset = saved['source']['image_pool'][0]
        assert asset['path'].startswith('data/images/')
        assert (context.paths.app_dir / asset['path']).read_bytes() == png()
        with urllib.request.urlopen(urllib.request.Request(base + asset['preview_url'], headers=auth_headers)) as response:
            assert response.read() == png()
        post('/api/desktop/disconnect', {}, connection)
        failed = post('/api/collect-resume', {'source_url': URL, 'browser_tab_id': 'original'})
        assert not failed['ok'] and failed['diagnostics']['error_code'] == 'DESKTOP_UNAVAILABLE'
        assert context.products.load_product_from_index(saved['product_id'])['source']['title'] == '采集测试商品'
        # 桌面断开后，后端普通读取仍使用原数据。
        with urllib.request.urlopen(urllib.request.Request(base + '/api/products-index', headers=auth_headers)) as response:
            assert json.load(response)['items']
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


@pytest.mark.parametrize('sent', [False, True])
def test_disconnect_ends_calls_without_replay_or_cross_desktop_fallback(sent):
    broker = DesktopConnections()
    first = broker.connect()['connection_id']
    second = broker.connect()['connection_id']
    enqueued = Event()
    def caller():
        with bind_desktop_connection(first):
            enqueued.set()
            return broker.call('browser.status', timeout=3, params={})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(caller)
            assert enqueued.wait(1)
            if sent:
                calls = broker.poll(first, wait_seconds=1)['calls']
                assert len(calls) == 1
            else:
                deadline = time.monotonic() + 1
                while not broker.connections[first].calls and time.monotonic() < deadline:
                    time.sleep(0.001)
            broker.disconnect(first)
            with pytest.raises(DesktopUnavailable) as error:
                future.result(timeout=2)
            assert error.value.outcome_unknown is sent
            assert broker.poll(second, wait_seconds=0)['calls'] == []
    finally:
        broker.close()


def test_native_cancellation_cancels_dispatched_local_call_and_ignores_late_result():
    broker = DesktopConnections()
    key = broker.connect()['connection_id']
    token = CancellationToken()
    def caller():
        with bind_desktop_connection(key), bind_cancellation_token(token):
            broker.call('cli.run', {'model': {'id': 'codex'}, 'prompt': '测试'}, timeout=3)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(caller)
            call = broker.poll(key, wait_seconds=1)['calls'][0]
            token.cancel()
            with pytest.raises(RunCancelled):
                future.result(timeout=2)
            assert broker.poll(key, wait_seconds=0)['calls'] == [{'id': call['id'], 'cancel': True}]
            assert not broker.complete(key, {'id': call['id'], 'ok': True, 'result': {'text': '过期结果'}})['accepted']
    finally:
        broker.close()


def test_local_model_configuration_is_not_saved_to_business_config(tmp_path):
    from desktop_runtime.config import LocalConfig
    from desktop_runtime.dispatcher import dispatch
    from erp_web.services import config_service
    from erp_web.services.desktop_connection_service import bind_desktop_connection
    from unittest.mock import patch
    local = LocalConfig(tmp_path / 'desktop')
    def call(operation, params=None, **kwargs):
        return dispatch(local, operation, params or {})
    incoming = {'ai_models': [{'id': 'local-browser', 'connection_type': 'browser',
                'browser_provider': 'gemini', 'browser_proxy': 'http://127.0.0.1:7890',
                'browser_profile': 'existing', 'browser_url': 'https://gemini.google.com/share/owned'}]}
    local.update({'local-browser': {key: value for key, value in incoming['ai_models'][0].items() if key in {'browser_proxy', 'browser_profile', 'browser_url'}}})
    with patch('erp_web.services.desktop_model_config_service.call_desktop', call), bind_desktop_connection('test'):
        merged = config_service.merge_ai_config(tmp_path, {}, incoming)
    model = merged['ai_models'][0]
    assert model['local_connection_id'] == 'local-browser'
    assert not {'browser_profile', 'browser_proxy', 'browser_url'} & model.keys()
    assert local.read()['local-browser']['browser_proxy'] == 'http://127.0.0.1:7890'
    assert local.model(model)['browser_profile'] == 'existing'
    local.update({'local-browser': {'browser_profile': 'changed'}})
    with pytest.raises(ValueError, match='配置已变化'):
        local.model(model)


def test_cli_image_probe_transfers_the_local_file_and_preserves_real_bytes(tmp_path, monkeypatch):
    from desktop_runtime import dispatcher
    from desktop_runtime.config import LocalConfig
    source = tmp_path / 'cli-result.png'
    source.write_bytes(png())
    monkeypatch.setattr(dispatcher, '_run_codex_cli_text', lambda *args: json.dumps({'can_generate_image': True, 'image_path': str(source)}))
    result = dispatcher.dispatch(LocalConfig(tmp_path / 'desktop'), 'cli.run', {
        'model': {'id': 'codex'}, 'prompt': '生成测试图片', 'probe_image': True,
    })
    output = json.loads(result['text'])
    assert 'image_path' not in output
    assert base64.b64decode(output['image_base64']) == png()


def test_backend_cannot_replace_local_command_through_model_parameters(tmp_path, monkeypatch):
    from desktop_runtime import dispatcher
    from desktop_runtime.config import LocalConfig
    local = LocalConfig(tmp_path)
    local.update({'cli': {'command': '/trusted/codex'}})
    observed = []
    monkeypatch.setattr(dispatcher, '_run_codex_cli_text', lambda directory, model, *args: observed.append(model['command']) or '{}')
    dispatcher.dispatch(local, 'cli.run', {'model': {'id': 'cli', 'command': 'untrusted command'}, 'prompt': '测试'})
    assert observed == ['/trusted/codex']


def test_native_deferred_job_keeps_the_initiating_desktop_when_other_pages_connect(tmp_path):
    import asyncio
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from erp_web.services.desktop_connection_service import current_desktop_connection
    from erp_web.services.agent_job_service import AgentJobService
    from tests.test_native_agent_integration import binding, body, service, tools
    observed = []
    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield '已完成。'
        else:
            yield {0: DeltaToolCall(name='local_job', json_args='{"draft_id":"selected"}', tool_call_id='owned-call')}
    ui = service(tmp_path, FunctionModel(stream_function=model), tools(binding(
        'local_job', lambda args, ctx: observed.append(current_desktop_connection()) or {'ok': True}, external=True, write=True)))
    with bind_desktop_connection('initiating-desktop'):
        asyncio.run(ui.prepare_run(body()).stream(lambda _: None))
    row = ui.call_store.work()[0]
    assert json.loads(row['execution_json'])['desktop_connection'] == 'initiating-desktop'
    jobs = AgentJobService(ui_service=ui, job_readers={})
    try:
        with bind_desktop_connection('another-desktop'):
            jobs.execute_or_reconcile(row)
        assert observed == ['initiating-desktop']
        receipt = ui.call_store.receipt(row['conversation_id'], row['tool_call_id'])
        assert receipt['status'] == 'completed'
    finally:
        jobs.close()


def test_background_image_batch_keeps_original_desktop_after_page_context_changes():
    from erp_web.services.desktop_connection_service import current_desktop_connection
    from erp_web.services.image_batch_service import run_image_batch
    context = get_context()
    observed = []
    completed = Event()
    def process(source):
        observed.append(current_desktop_connection())
        return {'ok': True, 'imagePoolItems': [{'id': 'generated', 'preview_url': '/file?path=generated.png'}]}
    def persist(result):
        observed.append(current_desktop_connection())
        completed.set()
    with bind_desktop_connection('initiating-desktop'):
        result = run_image_batch(store=context.image_batches, messages=context.pydantic_messages,
            app_dir=context.paths.app_dir, product_id='product', prompt='修改图片',
            sources=[{'id': 'original'}], process=process, persist=persist, background=True)
    with bind_desktop_connection('another-desktop'):
        assert completed.wait(3)
    assert result['ok']
    assert observed == ['initiating-desktop', 'initiating-desktop']


def test_local_cli_deadline_terminates_only_the_started_process(tmp_path):
    from desktop_runtime.cli import _run_codex_cli_text
    import shlex
    script = tmp_path / 'cli-fixture.py'
    pid = tmp_path / 'owned.pid'
    script.write_text(f'import os,time\nfrom pathlib import Path\nPath({str(pid)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n')
    with pytest.raises(TimeoutError, match='超时'):
        _run_codex_cli_text(tmp_path, {'command': f'{shlex.quote(sys.executable)} {shlex.quote(str(script))}'}, '测试取消', 1)
    child_id = int(pid.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(child_id, 0)


def test_model_transport_cannot_override_local_browser_url_with_an_alias(tmp_path):
    from desktop_runtime.config import LocalConfig
    config = LocalConfig(tmp_path)
    config.update({'browser': {'browser_url': 'https://gemini.google.com/share/owned'}})
    effective = config.model({'id': 'browser', 'url': 'http://untrusted.test', 'browser_url': 'http://untrusted.test'})
    assert effective['browser_url'] == 'https://gemini.google.com/share/owned'
    assert 'url' not in effective


def test_missing_canvas_connection_uses_only_matching_public_preset(tmp_path, monkeypatch):
    from desktop_runtime.config import LocalConfig
    from erp_shared.desktop import LOCAL_MODEL_FIELDS, local_configuration_fingerprint
    from erp_web.services import ai_model_config, desktop_model_config_service as settings

    config = LocalConfig(tmp_path)
    preset = ai_model_config.browser_model_presets()[0]["model"]
    fields = {key: preset[key] for key in LOCAL_MODEL_FIELDS if key in preset}
    binding = {key: value for key, value in preset.items() if key not in LOCAL_MODEL_FIELDS}
    binding["local_connection_id"] = binding["id"]
    binding["local_config_fingerprint"] = local_configuration_fingerprint(fields)
    monkeypatch.setattr(settings, "current_desktop_connection", lambda: "dev")
    monkeypatch.setattr(settings, "call_desktop", lambda *args, **kwargs: {"models": config.read()})

    assert config.model({**binding, "browser_url": "https://untrusted.test"})["browser_url"] == fields["browser_url"]
    assert settings.local_models_view([binding])[0]["browser_url"] == fields["browser_url"]
    assert config.read() == {}
    custom = {**binding, "local_config_fingerprint": local_configuration_fingerprint({**fields, "browser_profile": "other"})}
    assert settings.prepare_local_model(custom) == custom
    assert "browser_url" not in settings.local_models_view([custom])[0]
    with pytest.raises(ValueError, match="尚未配置"):
        config.model(custom)
    config.update({binding["id"]: {"browser_profile": "mine"}})
    with pytest.raises(ValueError, match="已变化"):
        config.model(binding)

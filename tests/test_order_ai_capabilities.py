"""订单工具复用真实领域服务，以替身验证审批、账号隔离和采购防重。"""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.messages import ToolReturnPart

from erp_web.ai_capability_composition import APPLICATION_CAPABILITY_CATALOG
from erp_web.context import get_context
from erp_web.facades.agent_capability_facade import build_global_chat_toolset
from erp_web.runtime_units.order_capability_support import OrderCapabilityScope
from erp_web.runtime_units.order_capabilities import order_detail_read, order_purchase_record, _record_snapshot
from erp_web.runtime_units.order_purchase_capabilities import (
    order_alibaba_create, order_alibaba_pay, order_alibaba_preview,
    _create_snapshot, _pay_snapshot,
)
from erp_web.runtime_units.order_fulfillment_capabilities import (
    order_fulfillment_update, _fulfillment_snapshot, order_receiving_warehouse_read,
    order_receiving_warehouse_update, _receiving_snapshot,
)
from erp_web.schemas.receiving_warehouse import ReceivingWarehouseRequest
from erp_web.schemas.ai_tools import AiToolExecutionError, AiToolSchemaError, validate_json_schema
from erp_web.schemas.alibaba_self_purchase import SelfPurchaseCreateRequest, SelfPurchasePayRequest, SelfPurchasePreviewRequest
from erp_web.schemas.order_capabilities import OrderRequest, OrderFulfillmentRequest, OrderPurchaseRecordRequest, OrderNotificationRequest
from erp_web.services.capability_errors import BusinessCapabilityError
from erp_web.services.alibaba_self_purchase_client import CREATE, PAY
from tests.approval_support import _approved_execution, _execution
from tests.test_alibaba_self_purchase import setup_purchase, domain, preview, target, NUMBER
from tests.test_alibaba_purchase_payment import payment
from tests.test_fulfillment import domain as fulfillment_domain, ready
from tests.test_native_agent_integration import CONVERSATION, body, service as chat_service


def scope_for(purchase=None, *, procurement=None, fulfillment=None):
    procurement = procurement or purchase.procurement
    fulfillment = fulfillment or SimpleNamespace(receiving=purchase.receiving)
    return OrderCapabilityScope(
        orders=lambda: SimpleNamespace(store=procurement.store.orders, accounts=procurement.accounts_provider),
        procurement=lambda: procurement, fulfillment=lambda: fulfillment, purchase=lambda: purchase,
        summary=lambda: procurement.store.orders.summary(procurement.accounts_provider()),
        procurement_command=lambda action, body: {
            "select-source": procurement.select_source, "record-purchase": procurement.record_purchase,
            "cancel-purchase": procurement.cancel_purchase,
        }[action](body),
        purchase_command=lambda action, body: getattr(purchase, action)(body),
        purchase_query=lambda body: pytest.fail("本地读取不得外发"),
        purchase_sync=lambda body: pytest.fail("本地读取不得同步"),
    )


def test_order_tools_are_bound_to_main_agent_and_keep_approval_boundaries():
    bindings = build_global_chat_toolset().bindings
    names = {name for name in APPLICATION_CAPABILITY_CATALOG.tools if name.startswith(('order_', 'orders_'))}
    assert len(names) == 29 and names <= bindings.keys()
    for name in ('order_alibaba_create', 'order_alibaba_pay', 'order_fulfillment_update', 'order_receiving_warehouse_update'):
        assert bindings[name].definition.approval_required
        assert bindings[name].approval_preparer is not None
        assert bindings[name].definition.recovery_policy == 'manual'
    assert 'request_id' not in bindings['order_purchase_record'].definition.input_schema['properties']
    assert 'pay_channel' not in bindings['order_alibaba_pay'].definition.input_schema['properties']
    assert not any('authorize' in name for name in names)


def test_notification_and_alert_identifiers_cannot_be_mixed():
    assert OrderNotificationRequest(action='retry', event_id=4).through_id is None
    assert OrderNotificationRequest(action='acknowledge', through_id=9).event_id is None
    for payload in ({'action': 'retry', 'through_id': 9}, {'action': 'acknowledge', 'event_id': 4},
                    {'action': 'retry', 'event_id': 4, 'through_id': 9}):
        with pytest.raises(ValidationError):
            OrderNotificationRequest.model_validate(payload)


def test_order_read_uses_same_snapshot_and_account_checks(setup_purchase):
    purchase, client, _, (order, _) = setup_purchase
    scope = scope_for(purchase)
    before = list(client.calls)
    result = order_detail_read(OrderRequest(order_id=order.identity), scope)
    assert result.data.model_dump(mode='json') == purchase.procurement.detail(order.identity)
    assert client.calls == before
    purchase.procurement.accounts_provider = lambda: {'yandex': 'another-account'}
    with pytest.raises(BusinessCapabilityError):
        order_detail_read(OrderRequest(order_id=order.identity), scope)


def test_preview_tool_reuses_frozen_domain_result(setup_purchase):
    purchase, client, _, _ = setup_purchase
    scope = scope_for(purchase)
    request = SelfPurchasePreviewRequest(**target(purchase), candidate_id='1', quantity=1, receiving_revision=1)
    result = order_alibaba_preview(request, scope, _execution())
    assert result.record['preview']['total_fen'] == 1580
    assert result.record['state'] == 'preview'
    assert not any(name == CREATE for name, _ in client.calls)


def test_create_requires_approval_binds_channel_and_never_pays(setup_purchase):
    purchase, client, _, _ = setup_purchase
    scope = scope_for(purchase)
    request = SelfPurchaseCreateRequest(preview_id=preview(purchase)['id'], pay_channel='shegou')
    frozen = _create_snapshot(request, scope)
    assert '1580' in frozen.summary and '蓝色' in frozen.summary and '不付款' in frozen.summary
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_alibaba_create(request, scope, _execution())
    approved = _approved_execution(frozen, 'order_alibaba_create')
    with pytest.raises(AiToolExecutionError, match='失效'):
        order_alibaba_create(request.model_copy(update={'pay_channel': 'alipay'}), scope, approved)
    assert not any(name == CREATE for name, _ in client.calls)
    result = order_alibaba_create(request, scope, approved)
    assert result.record['state'] == 'created' and result.record['order_numbers'] == [NUMBER]
    assert not any(name == PAY for name, _ in client.calls)
    # 新调用即使再次获得审批，领域请求身份仍阻止重复下单。
    order_alibaba_create(request, scope, _approved_execution(_create_snapshot(request, scope), 'order_alibaba_create'))
    assert sum(name == CREATE for name, _ in client.calls) == 1


def test_unknown_create_is_returned_as_unknown_and_never_recreated(setup_purchase):
    purchase, client, _, _ = setup_purchase
    scope = scope_for(purchase)
    request = SelfPurchaseCreateRequest(preview_id=preview(purchase)['id'], pay_channel='alipay')
    client.fail_create = True
    result = order_alibaba_create(request, scope, _approved_execution(_create_snapshot(request, scope), 'order_alibaba_create'))
    assert result.record['state'] == 'unknown' and not result.record['order_numbers']
    order_alibaba_create(request, scope, _approved_execution(_create_snapshot(request, scope), 'order_alibaba_create'))
    assert sum(name == CREATE for name, _ in client.calls) == 1


def test_payment_has_separate_approval_and_persistent_deduplication(payment):
    purchase, client, _, payload = payment
    scope = scope_for(purchase)
    request = SelfPurchasePayRequest(**payload)
    frozen = _pay_snapshot(request, scope)
    assert NUMBER in frozen.summary and client.channel in frozen.summary
    assert ('直接扣款' if client.channel == 'alipay' else '先采后付') in frozen.summary
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_alibaba_pay(request, scope, _execution())
    result = order_alibaba_pay(request, scope, _approved_execution(frozen, 'order_alibaba_pay'))
    assert result.record['payment']['state'] == 'succeeded'
    order_alibaba_pay(request, scope, _approved_execution(_pay_snapshot(request, scope), 'order_alibaba_pay'))
    assert sum(name == PAY for name, _ in client.calls) == 1


@pytest.mark.parametrize('change', ['amount', 'channel', 'address', 'account', 'sales_order'])
def test_payment_rejects_changed_frozen_facts_before_network(payment, change):
    purchase, client, config, payload = payment
    scope = scope_for(purchase)
    request = SelfPurchasePayRequest(**payload)
    approved = _approved_execution(_pay_snapshot(request, scope), 'order_alibaba_pay')
    before = len(client.calls)
    if change == 'account':
        config['access_token'] = 'changed'
    elif change == 'sales_order':
        original_detail = purchase.procurement.detail
        def cancelled(order_id):
            detail = deepcopy(original_detail(order_id))
            detail['order']['state'] = 'cancelled'
            return detail
        purchase.procurement.detail = cancelled
    else:
        original = purchase.approval_facts
        def changed(ident):
            facts = deepcopy(original(ident))
            record = facts['record']
            if change == 'amount': record['preview']['total_fen'] += 1
            if change == 'channel': record['pay_channel'] = 'alipay' if client.channel == 'shegou' else 'shegou'
            if change == 'address': record['preview']['address'] = '另一地址'
            return facts
        purchase.approval_facts = changed
    with pytest.raises((AiToolExecutionError, BusinessCapabilityError)):
        order_alibaba_pay(request, scope, approved)
    assert len(client.calls) == before


def test_manual_purchase_uses_trusted_request_identity(setup_purchase):
    purchase, _, _, (order, key) = setup_purchase
    scope = scope_for(purchase)
    request = OrderPurchaseRecordRequest(order_id=order.identity, source_platform='1688', purchase_order_number='manual-1')
    result = order_purchase_record(request, scope, _approved_execution(_record_snapshot(request, scope), 'order_purchase_record'))
    record = result.data.purchases[0]
    assert record.request_id.startswith('ai-order-') and record.items == []
    assert result.data.lines[0].remaining_quantity == 2


def test_fulfillment_approval_binds_revision_and_actual_packages(fulfillment_domain):
    fulfillment, bus, order = ready(fulfillment_domain)
    procurement = SimpleNamespace(detail=fulfillment.detail_provider)
    scope = scope_for(procurement=procurement, fulfillment=fulfillment)
    request = OrderFulfillmentRequest(order_id=order.identity, revision=fulfillment.detail(order.identity)['revision'], action='submit')
    frozen = _fulfillment_snapshot(request, scope)
    assert 'A仓' in frozen.summary and '商品数量：2' in frozen.summary
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_fulfillment_update(request, scope, _execution())
    approved = _approved_execution(frozen, 'order_fulfillment_update')
    fulfillment.command('pause', {'order_id': order.identity, 'revision': request.revision})
    with pytest.raises(BusinessCapabilityError, match='变化'):
        order_fulfillment_update(request, scope, approved)
    assert bus.calls == []
    view = fulfillment.command('resume', {'order_id': order.identity, 'revision': fulfillment.detail(order.identity)['revision']})
    request = request.model_copy(update={'revision': view['revision']})
    result = order_fulfillment_update(request, scope, _approved_execution(_fulfillment_snapshot(request, scope), 'order_fulfillment_update'))
    assert result.data.crossborderbus_order_id is None
    assert bus.calls == []  # 工具只提交既有领域后台，不把入队回执当作远端完成。


@pytest.mark.parametrize('legacy_version', [None, 0, 999])
def test_ai_plan_uses_approved_receiving_version_without_model_bookkeeping(fulfillment_domain, legacy_version):
    from tests.receiving_warehouse_support import assign_receiving

    fulfillment, bus, order, *_ = fulfillment_domain
    assign_receiving(fulfillment.receiving, order)
    scope = scope_for(procurement=SimpleNamespace(detail=fulfillment.detail_provider), fulfillment=fulfillment)
    current = fulfillment.detail(order.identity)
    body = dict(order_id=order.identity, revision=current['revision'], action='plan',
                plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    if legacy_version is not None:
        body['receiving_revision'] = legacy_version
    request = OrderFulfillmentRequest.model_validate(body)
    frozen = _fulfillment_snapshot(request, scope)
    before = fulfillment.store.get(order.identity)
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_fulfillment_update(request, scope, _execution())
    assert fulfillment.store.get(order.identity) == before
    result = order_fulfillment_update(request, scope, _approved_execution(frozen, 'order_fulfillment_update'))
    assert result.data.override and result.data.plan.service_ids == [2]
    assert fulfillment.store.get(order.identity)['receiving_revision'] == current['receiving_warehouse']['revision'] == 2
    assert bus.calls == []


@pytest.mark.parametrize('change', ['binding', 'configuration', 'after_verification'])
def test_ai_plan_keeps_approval_and_business_version_guards(fulfillment_domain, monkeypatch, change):
    from tests.receiving_warehouse_support import assign_receiving
    from tests.test_logistics_settings import edit

    fulfillment, bus, order, *_ = fulfillment_domain
    scope = scope_for(procurement=SimpleNamespace(detail=fulfillment.detail_provider), fulfillment=fulfillment)
    current = fulfillment.detail(order.identity)
    request = OrderFulfillmentRequest(order_id=order.identity, revision=current['revision'], action='plan',
                                     plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    approved = _approved_execution(_fulfillment_snapshot(request, scope), 'order_fulfillment_update')
    before = fulfillment.store.get(order.identity)
    if change == 'binding':
        assign_receiving(fulfillment.receiving, order)
    elif change == 'configuration':
        configured = fulfillment.receiving.logistics.detail()['addresses'][0]
        edit(fulfillment.receiving, configured, warehouse_id=20)
    else:
        command = fulfillment.command

        def changed(action, body):
            assign_receiving(fulfillment.receiving, order)
            return command(action, body)

        monkeypatch.setattr(fulfillment, 'command', changed)
    error = BusinessCapabilityError if change == 'after_verification' else AiToolExecutionError
    with pytest.raises(error):
        order_fulfillment_update(request, scope, approved)
    assert fulfillment.store.get(order.identity) == before
    assert bus.calls == []


def test_ai_can_rebind_stale_purchased_order_only_after_current_approval(fulfillment_domain):
    from tests.test_logistics_settings import edit

    fulfillment, bus, order = ready(fulfillment_domain)
    scope = scope_for(procurement=SimpleNamespace(detail=fulfillment.detail_provider), fulfillment=fulfillment)
    configured = fulfillment.receiving.logistics.detail()['addresses'][0]
    changed = edit(fulfillment.receiving, configured, warehouse_id=20,
                   address={**configured['address'], 'address': '新收货地址'})
    view = order_receiving_warehouse_read(OrderRequest(order_id=order.identity), scope).data
    assert view.binding_stale and not view.locked and view.has_purchases
    assert view.needs_rebind
    request = ReceivingWarehouseRequest(order_id=order.identity, revision=view.revision,
        address_id=changed['id'], address_revision=changed['revision'], section_id=1,
        delivery_key=view.delivery_key, confirmed=True, existing_purchase_confirmed=True)
    approved = _approved_execution(_receiving_snapshot(request, scope), 'order_receiving_warehouse_update')
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_receiving_warehouse_update(request, scope, _execution())
    changed = edit(fulfillment.receiving, changed, address={**configured['address'], 'address': '再次核对的地址'})
    with pytest.raises(BusinessCapabilityError, match='版本已变化'):
        order_receiving_warehouse_update(request, scope, approved)
    request = request.model_copy(update={'address_revision': changed['revision']})
    approved = _approved_execution(_receiving_snapshot(request, scope), 'order_receiving_warehouse_update')
    result = order_receiving_warehouse_update(request, scope, approved).data
    assert not result.binding_stale and result.warehouse.warehouse_id == 20
    assert result.warehouse.address.address == '再次核对的地址'
    assert bus.calls == []


def test_ai_receiving_query_counts_only_two_active_rebinding_orders(fulfillment_domain):
    from tests.receiving_warehouse_support import assign_receiving
    from tests.test_logistics_settings import edit

    fulfillment, bus, original, _, snapshot, _ = fulfillment_domain
    scope = scope_for(procurement=SimpleNamespace(detail=fulfillment.detail_provider), fulfillment=fulfillment)
    targets = []
    for number, state in [('62980209539', 'pending_shipment'), ('63000379456', 'pending_shipment'),
                          ('62666978435', 'cancelled'), ('62998783232', 'cancelled')]:
        order = original.model_copy(update={'order_id': number, 'state': state})
        snapshot(order)
        current = fulfillment.store.ensure(order)
        if state == 'pending_shipment':
            assign_receiving(fulfillment.receiving, order)
        else:
            fulfillment.store.change(order.identity, current['revision'], {'fulfillment_status': 'CANCELLED'})
        targets.append(order)
    configured = fulfillment.receiving.logistics.detail()['addresses'][0]
    edit(fulfillment.receiving, configured, warehouse_id=20)
    views = [order_receiving_warehouse_read(OrderRequest(order_id=o.identity), scope).data for o in targets]
    assert [o.order_id for o, v in zip(targets, views) if v.needs_rebind] == ['62980209539', '63000379456']
    for view in views[2:]:
        assert view.warehouse is None and not view.binding_stale and not view.can_confirm
        assert view.cancelled_fulfillment and '平台订单已取消' in view.blocked_reason
    assert bus.calls == []


@pytest.mark.parametrize('entry', ['direct', 'python'])
def test_native_main_agent_reads_current_order_through_real_toolset(setup_purchase, tmp_path, entry):
    purchase, _, _, (order, _) = setup_purchase
    app = get_context()
    app._order_procurement = purchase.procurement
    instructions = []
    outputs = []
    async def model(messages, info):
        instructions.append(info.instructions)
        outputs.extend(part.content for message in messages for part in message.parts if isinstance(part, ToolReturnPart))
        if len(instructions) == 1:
            if entry == 'direct':
                yield {0: DeltaToolCall(name='order_detail_read', tool_call_id='read-order', json_args=json.dumps({'order_id': order.identity}))}
            else:
                yield {0: DeltaToolCall(name='run_code', tool_call_id='read-order', json_args=json.dumps({'code':
                    f"r = await order_detail_read(order_id={order.identity!r})\nr['data']['order']['order_id']"}))}
        else:
            yield '订单已读取'
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    chunks = []
    asyncio.run(ui.prepare_run(body('查看这笔订单', page_context={'page': 'orders', 'order_id': order.identity})).stream(chunks.append))
    assert len(instructions) == 2 and order.identity in instructions[0]
    assert 'TOOL_OUTPUT_SCHEMA_INVALID' not in b''.join(chunks).decode()
    assert outputs and (outputs[-1]['data']['order']['order_id'] if entry == 'direct' else outputs[-1]) == order.order_id


@pytest.mark.parametrize('mode,approve', [('ask', True), ('ask', False), ('full', True)])
def test_native_approval_creates_only_after_decision(setup_purchase, tmp_path, monkeypatch, mode, approve):
    from erp_web.facades import alibaba_self_purchase_facade

    purchase, client, _, _ = setup_purchase
    record = preview(purchase)
    app = get_context()
    app._order_procurement = purchase.procurement
    app._fulfillment = SimpleNamespace(receiving=purchase.receiving, close=lambda: None)
    monkeypatch.setattr(alibaba_self_purchase_facade, 'create_service', lambda context: purchase)
    monkeypatch.setattr(alibaba_self_purchase_facade, 'sync_purchases', lambda *args, **kwargs: None)
    requests, outputs = [], []
    async def model(messages, info):
        requests.append(1)
        returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        if returned:
            outputs.extend(part.content for part in returned)
            yield '采购处理结果已返回'
        else:
            yield {0: DeltaToolCall(name='order_alibaba_create', tool_call_id='create-order', json_args=json.dumps({
                'preview_id': record['id'], 'pay_channel': 'alipay',
            }))}
    ui = chat_service(tmp_path, FunctionModel(stream_function=model), build_global_chat_toolset(app))
    ui.chat_service.approval_mode_reader = lambda: mode
    asyncio.run(ui.prepare_run(body('采购一件蓝色商品')).stream(lambda _: None))
    if mode == 'ask':
        assert not any(name == CREATE for name, _ in client.calls)
        parts = [part for message in ui.dump_ui_messages(CONVERSATION)['messages'] for part in message['parts']
                 if part.get('state') == 'approval-requested']
        assert len(parts) == 1
        parts[0]['state'] = 'approval-responded'
        parts[0]['approval']['approved'] = approve
        payload = json.dumps({'id': CONVERSATION, 'trigger': 'submit-message',
            'messages': [{'id': 'approval', 'role': 'assistant', 'parts': parts}]}).encode()
        asyncio.run(ui.prepare_run(payload, approval_token='test-token').stream(lambda _: None))
    assert sum(name == CREATE for name, _ in client.calls) == int(approve)
    assert not any(name == PAY for name, _ in client.calls)
    assert outputs
    if approve:
        assert outputs[-1]['record']['state'] == 'created'


@pytest.mark.parametrize('schema,good,bad', [
    ({'type': 'integer', 'exclusiveMinimum': 0}, 1, 0),
    ({'type': 'number', 'exclusiveMaximum': 1}, 0.5, 1),
    ({'type': 'string', 'pattern': '^$|^[A-Z]{2}$'}, 'RU', 'ru'),
])
def test_domain_constraints_survive_tool_schema_compilation(schema, good, bad):
    validate_json_schema(good, schema)
    with pytest.raises(AiToolSchemaError):
        validate_json_schema(bad, schema)
    definition = APPLICATION_CAPABILITY_CATALOG.tools['order_fulfillment_update'].definition
    assert definition.input_schema['properties']['country']['pattern'] == '^$|^[A-Z]{2}$'


def test_nested_label_request_keeps_agent_deadline_and_cancellation():
    from erp_web.services.external_request_context import request_context, request_operation

    calls = []
    with request_operation('ai_tool', deadline_at=100, cancellation_check=lambda: calls.append('agent')):
        with request_operation('fetch-label', deadline_at=250, cancellation_check=lambda: calls.append('label')):
            context = request_context('https://example.com/label.pdf')
            assert context.deadline_at == 100
            context.cancellation_check()
            assert calls == ['agent', 'label']
        assert request_context('https://example.com/label.pdf').deadline_at == 100
    assert request_context('https://example.com/label.pdf').deadline_at is None


def test_ai_cancelled_plan_preserves_binding_archives_once_and_requires_explicit_submit(fulfillment_domain):
    from tests.test_fulfillment import cancelled_fulfillment

    fulfillment, bus, order = cancelled_fulfillment(fulfillment_domain)
    scope = scope_for(procurement=SimpleNamespace(detail=fulfillment.detail_provider), fulfillment=fulfillment)
    current = fulfillment.detail(order.identity)
    binding = fulfillment.store.receiving.get(order.identity)
    request = OrderFulfillmentRequest(order_id=order.identity, revision=current['revision'], action='plan',
        plan={'section_id': 1, 'warehouse_id': 10, 'service_ids': [2]})
    frozen = _fulfillment_snapshot(request, scope)
    assert '归档原作废报单' in frozen.summary
    before = len(bus.calls)
    with pytest.raises(AiToolExecutionError, match='审批'):
        order_fulfillment_update(request, scope, _execution())
    assert len(bus.calls) == before
    result = order_fulfillment_update(request, scope, _approved_execution(frozen, 'order_fulfillment_update'))
    assert not result.data.receiving_binding_stale
    current = fulfillment.detail(order.identity)
    assert current['awaiting_resubmission'] and current['attempt_history'][0]['crossborderbus_order_id'] == 99
    assert current['crossborderbus_order_id'] is None
    assert fulfillment.store.receiving.get(order.identity) == binding
    assert [path for path, _ in bus.calls[before:]] == ['/erpapi/orderlist/status']
    assert not fulfillment.process_one(order)


def test_fulfillment_approval_does_not_depend_on_purchase_records(fulfillment_domain):
    fulfillment, _, order = ready(fulfillment_domain)
    procurement = fulfillment.detail_provider.__self__
    scope = scope_for(procurement=procurement, fulfillment=fulfillment)
    request = OrderFulfillmentRequest(order_id=order.identity, revision=fulfillment.detail(order.identity)['revision'], action='submit')
    approved = _approved_execution(_fulfillment_snapshot(request, scope), 'order_fulfillment_update')
    procurement.record_purchase({'order_id': order.identity, 'source_platform': 'other', 'purchase_order_number': 'EXTRA', 'request_id': 'extra'})
    result = order_fulfillment_update(request, scope, approved)
    assert result.data.crossborderbus_order_id is None
    assert fulfillment.store.get(order.identity)['manual_submit']

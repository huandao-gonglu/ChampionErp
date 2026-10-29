"""OGL 官方样本、分档、服务费、售价反推和历史迁移回归。"""
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path
import json

import pytest

from erp_web.international_shipping.ogl import electronic_fee, shipping
from erp_web.international_shipping.models import Package
from erp_web.international_shipping.tariff_store import default_snapshot, parse_template, load_active, read_version, digest, Workbook, parse_ozon
from erp_web.services.pricing_shipping import PricingShipping
from erp_web.services.pricing_service import pricing_result
from tests.pricing_test_utils import official_commission

SOURCE = Path(__file__).parent / 'fixtures' / 'ogl_20260824.xlsx'
COMMON = {'cost_cny': '35', 'weight_kg': '.3', 'length_cm': '20', 'width_cm': '10', 'height_cm': '5',
          'rub_cny_rate': '12', 'usd_cny_rate': '7', 'freight_cny': '3', 'prep_fee_cny': '2', 'other_cost_cny': '1'}
TARGET = {'platform': 'ozon', 'site': 'global', 'listing_currency': 'CNY', 'shipping_quote_mode': 'auto',
          'shipping_currency': 'CNY', 'target_margin_percent': '30'}


def calculate(tmp_path, *, common=None, target=None, price=None, commission=official_commission):
    selected = {**TARGET, **(target or {})}
    if price is not None:
        selected.update(pricing_mode='manual', manual_price={'amount': str(price), 'currency': selected['listing_currency']})
    resolver = PricingShipping(tmp_path, {})
    resolver.commission_resolver = commission
    return pricing_result({'common': {**COMMON, **(common or {})}, 'targets': [selected]},
                          shipping_resolver=resolver, commission_resolver=commission)['results'][0]


def test_official_workbook_matches_bundled_snapshot():
    parsed, _ = parse_template(SOURCE, 'ozon', '2026-08-24')
    assert parsed['content'] == default_snapshot()['content']
    assert parsed['version'] == default_snapshot()['version']


@pytest.mark.parametrize('group,weight,dims,expected,billable', [
    ('Extra Small',80,(10,8,5),'4.88',80), ('Extra Small',300,(20,10,5),'10.31',300),
    ('Small',1500,(20,10,5),'51.78',1500), ('Big',2200,(100,60,6),'84.02',3000),
    ('Budget',501,(200,200,200),'30.47',501), ('Premium Small',5000,(200,200,200),'142.28',5000),
    ('Premium Big',5001,(100,60,12),'193.17',6000),
])
def test_official_transport_samples(group,weight,dims,expected,billable):
    route = next(row for row in default_snapshot()['content']['rules'] if row['group']==group)
    amount, grams, _ = shipping(Package(weight,*dims),route)
    assert (amount,grams)==(D(expected),D(billable))


@pytest.mark.parametrize('price,weight,group', [
    ('135',500,'Extra Small'), ('135.01',500,'Small'), ('135.001',500,'Small'),
    ('135',501,'Budget'), ('635',2000,'Small'), ('635',2001,'Big'),
    ('635.01',5000,'Premium Small'), ('635.01',5001,'Premium Big'),
    ('22525',35000,'Premium Big'),
])
def test_price_weight_boundaries(tmp_path,price,weight,group):
    # 手动价格按店铺分价舍入，外币用更小的人民币步长覆盖转换后的边界。
    target = {'listing_currency':'RUB'} if price=='135.001' else {}
    result=calculate(tmp_path,price=D(price)*12 if target else price, common={'weight_kg':str(D(weight)/1000)},target=target)
    assert result['ok'], result['errors']
    assert result['calculation_basis']['shipping_evidence']['group']==group


@pytest.mark.parametrize('price,fee', [('1','1.25'),('62.5','1.25'),('100','2'),('1000',str(D(200)/12))])
def test_electronic_fee_clamp(price,fee):
    assert electronic_fee(price,12)==D(fee)


@pytest.mark.parametrize('common,price', [({'weight_kg':'35.001'},100),({'length_cm':201},100),
    ({'height_cm':0},100),({'weight_kg':'NaN'},100),({'weight_kg':'Infinity'},100),
    ({'weight_kg':-1},100),({'rub_cny_rate':''},100),({'rub_cny_rate':0},100),
    ({'rub_cny_rate':'bad'},100),({'cost_cny':'NaN'},100),({},0),({},22525.01),({},'bad100')])
def test_invalid_inputs_never_produce_applicable_quote(tmp_path,common,price):
    result=calculate(tmp_path,common=common,price=price)
    assert not result['ok']
    assert result['errors']


@pytest.mark.parametrize('cost,weight', [(1,'.08'),(35,'.3'),(60,'.3'),(100,'.3'),(280,'2.2'),(350,'5.001'),(800,'6')])
@pytest.mark.parametrize('mode', ['margin','markup'])
def test_reverse_price_meets_exact_goal_and_replays_same_cost(tmp_path,cost,weight,mode):
    result=calculate(tmp_path,common={'cost_cny':str(cost),'weight_kg':weight},target={'pricing_mode':mode,'markup_percent':30})
    assert result['ok'],result['errors']
    price=D(result['applied_price']['amount'])
    replay=calculate(tmp_path,common={'cost_cny':str(cost),'weight_kg':weight},price=price)
    assert replay['shipping_cost_cny']==result['shipping_cost_cny']
    assert replay['electronic_service_fee_cny']==result['electronic_service_fee_cny']
    exact_cost=D(cost)+6+D(str(result['shipping_cost_cny']))+electronic_fee(price,12)
    exact_profit=price*D('.8')-exact_cost
    assert exact_profit >= (price*D('.3') if mode=='margin' else exact_cost*D('.3'))
    assert abs(D(str(result['profit_cny']))-exact_profit)<=D('.0051')


def test_commission_drop_at_boundary_does_not_oscillate(tmp_path):
    def commission(common,target,price):
        amount=D(price['amount'])
        reply=official_commission(common,{**target,'commission_percent':30 if amount<100 else 10},price)
        reply['price_bands']=[{'rate':'.3','lower':'0','upper':'100'},{'rate':'.1','lower':'100','upper':None}]
        return reply
    result=calculate(tmp_path,common={'cost_cny':40},commission=commission)
    assert result['ok'],result['errors']
    assert result['applied_price']['amount']=='100.00'


def test_old_manual_freight_does_not_bypass_ogl(tmp_path):
    result=calculate(tmp_path,target={'shipping_quote_mode':'manual','shipping_amount':999})
    assert result['ok'] and result['shipping_cost_cny']==10.31
    assert result['shipping_quote_mode']=='auto'
    assert result['electronic_service_fee_cny']>0


def test_old_current_tariff_migrates_without_deleting_history(tmp_path):
    content={'schema_version':1,'platform':'ozon','effective_date':None,'policies':{},'rules':[]}
    version='ozon-'+digest(content)[:20]
    pack={'version':version,'content':content}
    (tmp_path/'versions').mkdir()
    path=tmp_path/'versions'/f'{version}.json'
    path.write_text(json.dumps(pack))
    (tmp_path/'current.json').write_text(json.dumps({'schema_version':1,'platforms':{'ozon':version}}))
    assert load_active(tmp_path,'ozon')['version']==default_snapshot()['version']
    assert read_version(tmp_path,version)==pack
    with pytest.raises(ValueError,match='旧第三方'):load_active(tmp_path,'ozon',version)


def test_pickup_self_drop_difference_rejected():
    book=Workbook(SOURCE)
    book.sheets[0]['rows'][12]['E']['value']='99'
    with pytest.raises(ValueError,match='自送与揽收'):parse_ozon(book)


@pytest.mark.parametrize('weight,cost', [('.3',1),('.3',60),('.3',100),('2.2',50),('5.001',300)])
def test_reverse_price_is_lowest_feasible_cent(tmp_path,weight,cost):
    result=calculate(tmp_path,common={'cost_cny':cost,'weight_kg':weight})
    assert result['ok'],result['errors']
    selected=D(result['applied_price']['amount'])
    grams=(D(weight)*1000).to_integral_value(rounding='ROUND_CEILING')
    package=Package(grams,20,10,5)
    routes=default_snapshot()['content']['rules']
    # 从独立的给定售价完整成本函数逐分枚举，验证没有跳过另一物流档的更低可行解。
    for cents in range(1,int(selected*100)+1):
        price=D(cents)/100
        matched=[row for row in routes if D(row['price_min_cny'])<=price<=D(row['price_max_cny'])
                 and D(row['min_g'])<=grams<=D(row['max_g'])]
        assert len(matched)==1
        transport=shipping(package,matched[0])[0]
        profit=price*D('.8')-D(cost)-6-transport-electronic_fee(price,12)
        if profit>=price*D('.3'):
            assert price==selected
            break
    else:
        pytest.fail('反推售价未满足完整费用的利润目标')


def test_old_quote_is_blocked_until_ogl_repricing():
    from tests.test_sku_workflow import product_fixture
    from erp_web.runtime_units.sku_publish_projection import sku_quote_errors
    product=product_fixture()
    draft=product['drafts']['ozon']; row=draft['sku_items'][0]
    basis=row['pricing']['targets']['ozon:global']['calculation_basis']
    basis.pop('electronic_service')
    assert 'OGL' in sku_quote_errors(product['sku_items'][0],row,draft,'ozon:global')[0]


def test_tariff_update_is_pinned_within_batch_and_visible_next_batch(tmp_path):
    from erp_web.international_shipping.tariff_store import import_template
    # 使用官方原表导入验证版本库启用与缺省内置版本的一致性。
    imported=import_template(SOURCE,'ozon',tmp_path,'2026-08-24')
    assert load_active(tmp_path,'ozon')['version']==imported['after_version']
    assert load_active(tmp_path,'ozon',imported['after_version'])['content']==default_snapshot()['content']
    from erp_web.international_shipping.service import ShippingModule
    module=ShippingModule(tmp_path, {}, opener=None)
    package=Package(300,20,10,5)
    def manual(*args):return D(100)
    before=module.quote('ozon',package,manual,cny_per_rub=D(1)/12)
    pack=deepcopy(default_snapshot())
    for route in pack['content']['rules']:
        route['fixed']=str(D(route['fixed'])+10)
    pack['version']='ozon-'+digest(pack['content'])[:20]
    (tmp_path/'versions'/f"{pack['version']}.json").write_text(json.dumps(pack))
    (tmp_path/'current.json').write_text(json.dumps({'schema_version':1,'platforms':{'ozon':pack['version']}}))
    same=module.quote('ozon',package,manual,cny_per_rub=D(1)/12)
    fresh=ShippingModule(tmp_path, {}, opener=None).quote('ozon',package,manual,cny_per_rub=D(1)/12)
    assert same.candidates[0].amount==before.candidates[0].amount
    assert fresh.candidates[0].amount==before.candidates[0].amount+10
    with pytest.raises(ValueError,match='生效日期'):parse_template(SOURCE,'ozon')


def test_parser_rejects_missing_group_changed_unit_and_formula():
    for column,value in [('G','1 kg'),('E',None),('C','Unknown')]:
        book=Workbook(SOURCE)
        cell=book.sheets[0]['rows'][6][column]
        if value is None: cell['formula']='2+3'
        else: cell['value']=value
        with pytest.raises(ValueError):parse_ozon(book)


def test_missing_rub_pair_uses_same_cross_rate_for_price_and_electronic_fee(tmp_path):
    common={'rub_cny_rate':'','currency_usd_rates':{'RUB':91,'USD':1},'usd_cny_rate':7}
    cross=calculate(tmp_path,common=common,target={'listing_currency':'RUB'})
    direct=calculate(tmp_path,common={'rub_cny_rate':13},target={'listing_currency':'RUB'})
    assert cross['ok'],cross['errors']
    assert cross['applied_price']==direct['applied_price']
    assert cross['electronic_service_fee_cny']==direct['electronic_service_fee_cny']
    assert cross['profit_cny']==direct['profit_cny']

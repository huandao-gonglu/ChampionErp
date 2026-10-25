"""缓存期限、容量和副本隔离。"""
from erp_web.services.pricing_cache import ExpiringCache


def test_cache_is_bounded_expires_and_does_not_extend_on_reads():
    now = [0]
    cache = ExpiringCache(ttl=10, capacity=2, clock=lambda: now[0])
    source = {'rules': ['原值']}
    cache.put('a', source)
    source['rules'].append('外部修改')
    cache.put('b', {})
    cached = cache.get('a')
    cached['rules'].append('读取方修改')
    assert cache.get('a') == {'rules': ['原值']}
    cache.put('c', {})
    assert cache.get('b') is None
    now[0] = 9
    assert cache.get('a') is not None
    now[0] = 10
    assert cache.get('a') is None
    cache.discard('c')
    assert cache.get('c') is None


def test_latest_uses_creation_time_and_does_not_extend_expiry():
    now = [0]
    cache = ExpiringCache(ttl=10, capacity=3, clock=lambda: now[0])
    cache.put('old', {'draft_id': 'draft', 'prices': [1]})
    now[0] = 1
    cache.put('new', {'draft_id': 'draft', 'prices': [2]})
    cache.get('old')
    match = lambda entry: entry['draft_id'] == 'draft'
    key, value = cache.latest(match)
    assert key == 'new'
    value['prices'].append(3)
    assert cache.latest(match)[1]['prices'] == [2]
    now[0] = 10
    assert cache.latest(match)[0] == 'new'
    now[0] = 11
    assert cache.latest(match) is None

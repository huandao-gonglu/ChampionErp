"""进程内核价缓存：规则只用于候选计算，预览只用于短期应用已核验售价。"""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic


class ExpiringCache:
    def __init__(self, *, ttl: float, capacity: int, clock=monotonic):
        self.ttl = ttl
        self.capacity = capacity
        self.clock = clock
        self._entries = OrderedDict()
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry[0] <= self.clock():
                self._entries.pop(key)
                return None
            self._entries.move_to_end(key)
            return deepcopy(entry[1])

    def put(self, key, value):
        with self._lock:
            now = self.clock()
            for old in [key for key, (expires, _) in self._entries.items() if expires <= now]:
                self._entries.pop(old)
            self._entries[key] = (now + self.ttl, deepcopy(value))
            self._entries.move_to_end(key)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def discard(self, key):
        with self._lock:
            self._entries.pop(key, None)

    def latest(self, predicate):
        """查找最近写入且未过期的匹配项；读取不延长有效期。"""
        with self._lock:
            now = self.clock()
            entries = [(expires, key, value) for key, (expires, value) in self._entries.items()
                       if expires > now and predicate(value)]
            if not entries:
                return None
            _, key, value = max(entries, key=lambda entry: entry[0])
            return key, deepcopy(value)


class PricingCache:
    def __init__(self):
        self.rules = ExpiringCache(ttl=3600, capacity=4096)
        self.previews = ExpiringCache(ttl=600, capacity=32)


__all__ = ["ExpiringCache", "PricingCache"]

"""商品聚合写入互斥；锁不跨模型调用或人工等待。"""

from contextlib import contextmanager
from functools import wraps
from inspect import signature
import threading

_registry_lock = threading.Lock()
_locks = {}


@contextmanager
def product_resource_locks(database_path, keys):
    selected = []
    with _registry_lock:
        for key in sorted(set(keys)):
            identity = (str(database_path), key)
            entry = _locks.setdefault(identity, [threading.RLock(), 0])
            entry[1] += 1
            selected.append((identity, entry))
    try:
        for _, entry in selected:
            entry[0].acquire()
        yield
    finally:
        for _, entry in reversed(selected):
            entry[0].release()
        with _registry_lock:
            for identity, entry in selected:
                entry[1] -= 1
                if entry[1] == 0:
                    del _locks[identity]


def product_mutation(kind):
    def decorate(function):
        parameters = signature(function)
        first_argument = next(name for name in parameters.parameters if name != "self")

        @wraps(function)
        def execute(self, *args, **kwargs):
            value = parameters.bind(self, *args, **kwargs).arguments[first_argument]
            arguments = value if isinstance(value, dict) else {kind + "_id": value}
            with self.mutation_scope(arguments):
                return function(self, *args, **kwargs)

        return execute

    return decorate

"""普通业务查询定义：同一声明派生页面入口和共用数据目录。"""
from dataclasses import dataclass, replace
from functools import partial
from types import MappingProxyType

from erp_web.services.business_data import BusinessDataCatalog, BusinessResource


@dataclass(frozen=True)
class BusinessQuery(BusinessResource):
    http_path: str = ''
    not_found_status: int = 400

    @property
    def path(self):
        return self.http_path or f'/api/business-queries/{self.name}'


class BusinessQueries:
    def __init__(self, definitions):
        self.definitions = tuple(definitions)
        names = [query.name for query in self.definitions]
        paths = [query.path for query in self.definitions]
        if len(set(names)) != len(names) or len(set(paths)) != len(paths):
            raise ValueError('业务查询名称或页面路径重复')
        if any(not path.startswith('/api/') or '?' in path or '#' in path for path in paths):
            raise ValueError('业务查询必须声明有效的 API 路径')
        self.http_queries = MappingProxyType(dict(zip(paths, names)))

    def bind(self, context):
        # 显式绑定当前账号业务上下文；发现目录时不调用任何查询。
        return BusinessDataCatalog(tuple(replace(query, loader=partial(query.loader, context))
                                         for query in self.definitions))


__all__ = ['BusinessQueries', 'BusinessQuery']

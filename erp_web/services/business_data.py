"""通用只读目录、字段投影与分页；不依赖具体平台、HTTP 或 Agent。"""
from dataclasses import dataclass
import json
from typing import Callable

from pydantic import BaseModel

from erp_web.schemas.business_data import DataCatalogRequest, DataCatalogResult, DataReadRequest, DataReadResult, DataResourceInfo


def at_path(value, path):
    if path in ('', '$'):
        return value
    for key in path.split('.'):
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError(f'数据字段不存在：{path}')
    return value


def project(row, fields, *, optional=False):
    if not fields:
        return row
    # 保留点路径作为列名，避免把缺失值误认为完整明细。
    result = {}
    for field in fields:
        try:
            result[field] = at_path(row, field)
        except ValueError:
            if not optional:
                raise
            result[field] = None
    return result


@dataclass(frozen=True)
class BusinessResource:
    name: str
    description: str
    parameters: type[BaseModel]
    loader: Callable
    default_path: str = ''
    default_fields: tuple[str, ...] = ()
    permission: str = 'business.read'


class BusinessDataCatalog:
    def __init__(self, resources):
        self.resources = {item.name: item for item in resources}
        if len(self.resources) != len(resources):
            raise ValueError('业务数据资源名重复')

    def resource(self, name):
        if name not in self.resources:
            raise ValueError('未知业务数据资源，请先查询目录')
        return self.resources[name]

    def load(self, name, params=None):
        resource = self.resource(name)
        return resource.loader(resource.parameters.model_validate(params or {}))

    def describe(self, request: DataCatalogRequest, *, permissions=None):
        rows = [self.resource(request.resource)] if request.resource else self.resources.values()
        if request.resource and permissions is not None and self.resource(request.resource).permission not in permissions:
            raise ValueError('无权读取此业务资源')
        return DataCatalogResult(resources=[DataResourceInfo(
            name=r.name, description=r.description, default_path=r.default_path,
            default_fields=list(r.default_fields), permission=r.permission, parameters=r.parameters.model_json_schema() if request.resource else {},
        ) for r in rows if (permissions is None or r.permission in permissions)
            and (not request.keyword or request.keyword.casefold() in (r.name + r.description).casefold())])

    def read(self, request: DataReadRequest, *, permissions=None):
        resource = self.resource(request.resource)
        if permissions is not None and resource.permission not in permissions:
            raise ValueError('无权读取此业务资源')
        full = self.load(request.resource, request.params)
        path = request.path or resource.default_path
        value = at_path(full, path)
        fields = request.fields or (list(resource.default_fields) if not request.path else [])
        metadata = {key: full[key] for key in ('version', 'source', 'filename', 'warnings', 'updated_at', 'preview_id', 'state') if isinstance(full, dict) and key in full}
        if len(json.dumps(metadata, ensure_ascii=False)) > 2000:
            metadata = {'notice': '元数据较大，请用 path=$ 和 fields 单独读取所需字段。'}
        if isinstance(value, str):
            if fields or request.filters:
                raise ValueError('文本字段不支持 fields 或 filters')
            chunk = value[request.offset:request.offset + 9000]
            end = request.offset + len(chunk)
            return DataReadResult(resource=request.resource, path=path, data=chunk, total=len(value), offset=request.offset,
                                  next_offset=end if end < len(value) else None, metadata=metadata,
                                  limited=end < len(value), notice='文本按字符位置分段，next_offset 可继续读取。')
        rows = value if isinstance(value, list) else [value]
        if request.filters:
            rows = [row for row in rows if all(at_path(row, key) == expected for key, expected in request.filters.items())]
        available = list(dict.fromkeys(key for row in rows[:50] if isinstance(row, dict) for key in row))[:40]
        result = DataReadResult(resource=request.resource, path=path, data=[], total=len(rows), offset=request.offset,
                                available_fields=available, metadata=metadata)
        selected = []
        for row in rows[request.offset:request.offset + request.limit]:
            candidate = project(row, fields, optional=not request.fields)
            result.data = [*selected, candidate]
            if len(result.model_dump_json()) > result.max_chars - 700:
                result.limited = True
                result.notice = '本页达到上下文预算；用 fields 选择字段，或用 path 展开单项明细。未返回的数据仍可继续读取。'
                break
            selected.append(candidate)
        result.data = selected if isinstance(value, list) else (selected[0] if selected else None)
        end = request.offset + len(selected)
        result.next_offset = end if end < len(rows) else None
        return result


__all__ = ['BusinessDataCatalog', 'BusinessResource']

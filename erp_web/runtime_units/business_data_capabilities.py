"""统一业务数据目录的薄 Agent 入口；资源和业务字段由普通查询层定义。"""
from dataclasses import dataclass
from typing import Annotated, Callable
from pydantic import ValidationError

from erp_web.schemas.business_data import DataCatalogRequest, DataCatalogResult, DataReadRequest, DataReadResult
from erp_web.services.ai_tool_declaration import Injected, ai_tool
from erp_web.services.business_data import BusinessDataCatalog
from erp_web.schemas.ai_trace import AiExecutionContext
from erp_web.services.capability_errors import BusinessCapabilityError


@dataclass(frozen=True)
class BusinessDataScope:
    catalog: Callable[[], BusinessDataCatalog]


@ai_tool(name='business_data_catalog', permission='business.read', description='发现普通业务数据目录：运费规则、尺寸重量限制、仓库配置、核价模板、草稿和 SKU 明细。先列目录，指定 resource 读取参数 Schema；无需为每种分析寻找专用工具。')
def business_data_catalog(request: DataCatalogRequest, scope: Annotated[BusinessDataScope, Injected()],
                          execution: Annotated[AiExecutionContext, Injected()]) -> DataCatalogResult:
    try:
        return scope.catalog().describe(request, permissions=execution.permissions)
    except ValueError as exc:
        raise BusinessCapabilityError('BUSINESS_DATA_QUERY_INVALID', str(exc)) from exc


@ai_tool(name='business_data_read', permission='business.read', description='按目录读取与页面共用的业务数据，不重新核价、不修改配置。params 填资源参数；默认摘要和分页，fields 选择字段、filters 精确筛选、path 展开字段或数组下标（$ 为根）。返回 available_fields 和 next_offset。询问为何选择物流时读取 draft.pricing 与 shipping.tariff_rules；检查历史依据和仓库配置，不能将当前规则冒充历史原因。多项比较用 Python 处理后只输出结论所需数据。')
def business_data_read(request: DataReadRequest, scope: Annotated[BusinessDataScope, Injected()],
                       execution: Annotated[AiExecutionContext, Injected()]) -> DataReadResult:
    try:
        return scope.catalog().read(request, permissions=execution.permissions)
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        raise BusinessCapabilityError('BUSINESS_DATA_QUERY_INVALID', str(exc), details={
            'resource': request.resource,
            'hint': '使用 business_data_catalog(resource=资源名) 核对参数；按返回的 path、available_fields、next_offset 读取，不猜测资源名或反复重试同一参数。',
        }) from exc


BUSINESS_DATA_CAPABILITIES = (business_data_catalog, business_data_read)
__all__ = ['BusinessDataScope', 'BUSINESS_DATA_CAPABILITIES']

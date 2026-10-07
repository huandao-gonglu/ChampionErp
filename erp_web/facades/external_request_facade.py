"""外部请求中断的本机界面入口；只返回脱敏投影。"""
from erp_web.schemas.external_request_control import RequestControlStatus
from erp_web.context import get_context
from erp_web.services.external_request_control_service import ExternalRequestControlService


def status(*, offset=0, operation_ids=()):
    return RequestControlStatus.model_validate(ExternalRequestControlService(get_context().external_requests.store).status(offset=offset, operation_ids=operation_ids)).model_dump(mode="json")


def recover(body):
    return ExternalRequestControlService(get_context().external_requests.store).recover(body['block_id'], reason=body.get('reason', ''))

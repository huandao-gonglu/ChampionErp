"""1688 业务调用的错误类型。"""


class AlibabaApiError(ValueError):
    """可向用户展示的业务错误，不携带请求凭据或完整回执。"""


class AlibabaApiRejected(AlibabaApiError):
    """明确未执行的操作，可释放本地占位。"""

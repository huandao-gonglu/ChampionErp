"""平台面单读取：复用店铺授权，只读面单和箱号，不自动分箱或改发货状态。"""

from __future__ import annotations

from erp_web.marketplaces.yandex_http import YandexApiError, request_yandex_json, request_yandex_pdf
from erp_web.schemas.fulfillment import FulfillmentError, LabelInput
from erp_web.services.fulfillment_label_service import deliver_label_bytes, validate_label_pdf
from erp_web.services.image_hosting_config import default_profile


class PlatformLabelService:
    def __init__(self, store_config_provider, app_config_provider):
        self.store_config_provider = store_config_provider
        self.app_config_provider = app_config_provider

    @staticmethod
    def unsupported_reason(order):
        if order.platform != "yandex":
            return "当前平台尚未接入面单自动获取，请上传平台 PDF 或填写面单地址。"
        if (order.delivery.fulfillment_model or order.fulfillment).upper() not in {"FBS", "DBS", "EXPRESS"}:
            return "当前 Yandex 履约模式不支持此面单接口。"
        if order.state != "pending_shipment":
            return "仅待发货的平台订单可以获取面单，请同步并核对订单状态。"
        return ""

    def credentials(self, order):
        config = self.store_config_provider().get("yandex", {})
        if str(config.get("campaign_id") or "") != order.account_id or not config.get("api_token"):
            raise FulfillmentError("Yandex 店铺授权已变化，请重新同步订单后获取面单。")
        return dict(config)

    def fetch(self, order) -> LabelInput:
        reason = self.unsupported_reason(order)
        if reason:
            raise FulfillmentError(reason)
        config = self.credentials(order)
        if not all(value.isascii() and value.isdigit() and int(value) > 0 for value in (order.account_id, order.order_id)):
            raise FulfillmentError("Yandex 店铺或订单标识无效，请重新同步订单。")
        app_config = self.app_config_provider()
        try:
            default_profile(app_config)
        except ValueError:
            raise FulfillmentError("请先在授权配置中设置可公开下载的默认 S3 托管，再获取面单。") from None
        path = f"/v2/campaigns/{order.account_id}/orders/{order.order_id}/delivery/labels"
        try:
            data = request_yandex_json("GET", path + "/data", config["api_token"])
            result = data.get("result") or {}
            boxes = result.get("parcelBoxLabels")
            if str(result.get("orderId") or "") != order.order_id:
                raise FulfillmentError("平台面单回执与当前订单不一致，已停止保存。")
            if not isinstance(boxes, list) or not boxes:
                raise FulfillmentError("Yandex 尚未生成面单，请在平台确认分箱后重新获取。")
            if result.get("placesNumber") != 1 or len(boxes) != 1:
                raise FulfillmentError("此订单包含多个国际箱，当前预报只支持一个面单号，请核对分箱并人工处理。")
            box = boxes[0]
            # fulfilmentId 是平台返回的箱条码；orderNum 是订单号，不能代替箱号。
            tracking = str(box.get("fulfilmentId") or "").strip()
            if str(box.get("orderId") or "") != order.order_id or not tracking:
                raise FulfillmentError("Yandex 面单缺少本订单对应的箱号，请核对平台分箱后重试。")
            pdf = validate_label_pdf(request_yandex_pdf(path, config["api_token"]))
        except YandexApiError as exc:
            if exc.http_status in {401, 403}:
                message = "Yandex 面单授权不足或失效，请检查 API-Key 的订单处理权限。"
            elif exc.http_status in {400, 404}:
                message = "Yandex 面单尚不可获取，请检查订单状态、分箱及店铺归属后重试。"
            elif exc.http_status in {420, 429}:
                message = "Yandex 面单接口被限流，请稍后重新获取。"
            else:
                message = "Yandex 面单请求失败，请检查网络或平台处理情况后重新获取。"
            raise FulfillmentError(message) from None
        if self.credentials(order)["api_token"] != config["api_token"]:
            raise FulfillmentError("Yandex 授权在获取期间发生变化，已停止保存面单。")
        url = deliver_label_bytes(app_config, order.identity, pdf)["url"]
        if self.credentials(order)["api_token"] != config["api_token"]:
            raise FulfillmentError("Yandex 授权在获取期间发生变化，已停止保存面单。")
        return LabelInput(url=url, tracking_number=tracking)

"""交货地址备注；映射独立于订单快照，不随平台同步覆盖。"""

from pydantic import BaseModel, ConfigDict, Field


class AddressNoteWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    order_id: str = Field(min_length=1, max_length=200)
    shipment_id: str = Field(min_length=1, max_length=100)
    address_key: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=0)
    note: str = Field(max_length=4000)


class AddressNoteView(BaseModel):
    ok: bool = True
    address_key: str
    address: str
    note: str = ""
    revision: int = 0
    updated_at: str = ""


class AddressNoteConflict(ValueError):
    def __init__(self, current: AddressNoteView):
        super().__init__("这条地址备注已被修改，请核对最新内容后再关闭保存")
        self.current = current

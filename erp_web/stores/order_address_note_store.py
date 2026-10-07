"""同店铺相同平台地址共享备注；事务内校验地址归属和备注版本。"""

import hashlib

from erp_web.schemas.order_address_notes import AddressNoteConflict, AddressNoteView, AddressNoteWrite
from erp_web.schemas.orders import OrderSnapshot, utc_iso
from erp_web.stores.order_notification_store import OrderNotificationStore


def normalized_address(address: str) -> str:
    # 只合并展示空白，不翻译、不去掉门牌、联系人或其他有业务含义的文字。
    return " ".join(address.split())


class OrderAddressNoteStore:
    def __init__(self, orders: OrderNotificationStore):
        self.orders = orders

    @staticmethod
    def _target(conn, order_id, shipment_id, accounts):
        row = conn.execute("SELECT snapshot FROM orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError("订单不存在")
        order = OrderSnapshot.model_validate_json(row[0])
        if accounts.get(order.platform) != order.account_id:
            raise ValueError("订单不属于当前店铺")
        shipments = order.handover.shipments if order.handover else []
        matched = [item for item in shipments if item.shipment_id == shipment_id]
        if len(matched) != 1:
            raise ValueError("订单交货批次已变化，请重新打开订单")
        shipment = matched[0]
        point = shipment.destination if shipment.shipment_type == "IMPORT" else shipment.origin if shipment.shipment_type == "WITHDRAW" else None
        if not point or not normalized_address(point.address):
            raise ValueError("该交货批次尚未提供可备注的地址")
        key = hashlib.sha256(normalized_address(point.address).encode()).hexdigest()
        return order.platform, order.account_id, key, point.address

    @staticmethod
    def _view(conn, target):
        platform, account, key, address = target
        row = conn.execute(
            "SELECT note,revision,updated_at FROM order_address_notes WHERE platform=? AND account_id=? AND address_key=?",
            (platform, account, key),
        ).fetchone()
        return AddressNoteView(address_key=key, address=address, **(dict(row) if row else {}))

    def read(self, order_id, shipment_id, accounts, *, address):
        with self.orders.connect() as conn:
            conn.execute("BEGIN")
            target = self._target(conn, order_id, shipment_id, accounts)
            if normalized_address(address) != normalized_address(target[3]):
                raise ValueError("平台交货地址已更新，请重新打开订单后备注")
            return self._view(conn, target)

    def save(self, body: AddressNoteWrite, accounts):
        with self.orders.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = self._target(conn, body.order_id, body.shipment_id, accounts)
            if target[2] != body.address_key:
                raise ValueError("平台交货地址已更新，备注未保存，请保留输入并重新打开订单")
            current = self._view(conn, target)
            # 超时后的重复提交可确认已保存，不能把重试当作另一次改写。
            if current.note == body.note:
                return current
            if current.revision != body.revision:
                raise AddressNoteConflict(current)
            updated = utc_iso()
            conn.execute(
                """INSERT INTO order_address_notes VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(platform,account_id,address_key) DO UPDATE SET
                address=excluded.address,note=excluded.note,revision=excluded.revision,updated_at=excluded.updated_at""",
                (*target[:3], target[3], body.note, current.revision + 1, updated),
            )
            conn.commit()
            return AddressNoteView(address_key=target[2], address=target[3], note=body.note,
                                   revision=current.revision + 1, updated_at=updated)

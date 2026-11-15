"""跨档口调拨服务。

状态机与数量口径：
  pending（待发）
    --dispatch(q)--> in_transit（在途）：调出确认，行锁校验可用量，不可超扣
  in_transit（在途）
    --receive(q)--> 仍 in_transit（部分接收）或 completed_received（全量接收结清）
    --return(q) --> 仍 in_transit（部分退回）或 completed_returned（全量退回结清）
    --receive + return 同笔请求只认一方；先部分接收后，只允许退回剩余在途量，
      退回后状态为 partially_settled（部分接收 + 余量退回，已结清）

恒等式（每笔订单始终成立）：
  dispatched_qty = received_qty + returned_qty + 在途余量
全局守恒由 kitchen.conservation_check 复核。
"""
import time
from fastapi import HTTPException
from sqlalchemy import select, func
from src.models import (
    TransferOrder, TransferEvent, Stall, Dish, PrepPlan,
)
from src.constants.enums import TransferStatus, TransferEvent as EventType
from src.services import kitchen
from src.services.idempotency import lock_rows

_SETTLED = {
    TransferStatus.completed_received.value,
    TransferStatus.completed_returned.value,
    TransferStatus.partially_settled.value,
}


def _gen_order_no(db) -> str:
    ts = time.strftime('%Y%m%d%H%M%S')
    count = db.scalar(select(func.count()).select_from(TransferOrder)) + 1
    return f'TR{ts}-{count:04d}'


def _add_event(db, order, event_type, qty, actor, detail=None):
    ev = TransferEvent(
        order_id=order.id, event_type=event_type, qty=qty,
        from_status=order.status if event_type != EventType.settle.value else '',
        to_status='', actor=actor, detail=detail or {}, created_at=time.time(),
    )
    db.add(ev)
    db.flush()
    return ev


def _on_road(order) -> int:
    return order.dispatched_qty - order.received_qty - order.returned_qty


def create_order(db, data, user):
    if data.request_qty <= 0:
        raise HTTPException(422, '调拨量必须为正整数')
    dish = kitchen._get(db, Dish, data.dish_id, '餐品')
    stalls = lock_rows(db, Stall, [data.from_stall_id, data.to_stall_id])
    by_id = {s.id: s for s in stalls}
    if data.from_stall_id == data.to_stall_id:
        raise HTTPException(422, '调出与接收档口不能相同')
    plan = None
    if data.plan_id is not None:
        plan = kitchen._get(db, PrepPlan, data.plan_id, '备餐方案')
        if plan.dish_id != dish.id or plan.stall_id != data.from_stall_id:
            raise HTTPException(422, '备餐方案与调出档口/餐品不一致')
    order = TransferOrder(
        order_no=_gen_order_no(db), plan_id=plan.id if plan else None,
        dish_id=dish.id, from_stall_id=data.from_stall_id,
        to_stall_id=data.to_stall_id, request_qty=data.request_qty,
        dispatched_qty=0, received_qty=0, returned_qty=0,
        status=TransferStatus.pending.value, note=data.note or '',
        created_by=user.id, created_at=time.time(), version=0,
    )
    db.add(order)
    db.flush()
    _add_event(db, order, EventType.settle.value, 0, user.email,
               {'action': 'create', 'request_qty': order.request_qty})
    return order


def dispatch(db, order_id, qty, actor):
    """调出确认：餐量进入在途。原子条件扣减，并发调出绝不超扣。"""
    order = lock_rows(db, TransferOrder, [order_id])[0]
    if order.status != TransferStatus.pending.value:
        raise HTTPException(409, f'当前状态「{order.status}」不能调出，仅待发单可调出')
    if qty <= 0:
        raise HTTPException(422, '调出量必须为正整数')
    if qty > order.request_qty:
        raise HTTPException(422, f'调出量 {qty} 超过申请量 {order.request_qty}')
    # 原子条件更新：仅当调出档口可用量 >= qty 时 dispatched 才增加，
    # 并发请求在此串行，最多一方成功，从根本上杜绝同一份餐超扣。
    kitchen.adjust_balance(
        db, order.from_stall_id, order.dish_id,
        {'dispatched_qty': qty}, require_available=qty,
    )
    order.dispatched_qty = qty
    order.status = TransferStatus.in_transit.value
    order.dispatched_at = time.time()
    order.version += 1
    db.flush()
    ev = _add_event(db, order, EventType.dispatch.value, qty, actor,
                    {'dispatched': qty, 'in_transit_after': qty})
    ev.to_status = order.status
    return order


def receive(db, order_id, qty, actor):
    """接收确认：计入接收档口可用量。接收与退回同一请求只认一方。"""
    order = lock_rows(db, TransferOrder, [order_id])[0]
    if order.status not in (TransferStatus.in_transit.value,):
        raise HTTPException(409, '仅在途订单可以接收确认')
    if qty <= 0:
        raise HTTPException(422, '接收量必须为正整数')
    road = _on_road(order)
    if qty > road:
        raise HTTPException(422, f'接收量 {qty} 超过在途余量 {road}')
    # 接收确认才计入接收档口可用量
    kitchen.adjust_balance(db, order.to_stall_id, order.dish_id,
                           {'received_qty': qty})
    order.received_qty += qty
    order.version += 1
    ev = _add_event(db, order, EventType.receive.value, qty, actor,
                    {'received_total': order.received_qty, 'on_road_after': _on_road(order)})
    if _on_road(order) == 0:
        # 在途清零：若此前发生过退回，则为部分接收结清，否则全量接收
        if order.returned_qty > 0:
            order.status = TransferStatus.partially_settled.value
        else:
            order.status = TransferStatus.completed_received.value
        order.settled_at = time.time()
        _add_event(db, order, EventType.settle.value, qty, actor,
                   {'reason': order.status})
    ev.to_status = order.status
    return order


def return_order(db, order_id, qty, actor):
    """退回确认：餐量回到原档口。部分接收后只允许退回余量。"""
    order = lock_rows(db, TransferOrder, [order_id])[0]
    if order.status not in (TransferStatus.in_transit.value,):
        raise HTTPException(409, '仅在途订单可以退回确认')
    if qty <= 0:
        raise HTTPException(422, '退回量必须为正整数')
    road = _on_road(order)
    if qty > road:
        raise HTTPException(422, f'退回量 {qty} 超过在途余量 {road}（部分接收后只退余量）')
    # 退回回到原档口：returned 增加，恢复其可用量
    kitchen.adjust_balance(db, order.from_stall_id, order.dish_id,
                           {'returned_qty': qty})
    order.returned_qty += qty
    order.version += 1
    ev = _add_event(db, order, EventType.return_.value, qty, actor,
                    {'returned_total': order.returned_qty, 'on_road_after': _on_road(order)})
    if _on_road(order) == 0:
        if order.received_qty > 0:
            order.status = TransferStatus.partially_settled.value
        else:
            order.status = TransferStatus.completed_returned.value
        order.settled_at = time.time()
        _add_event(db, order, EventType.settle.value, qty, actor,
                   {'reason': order.status})
    ev.to_status = order.status
    return order


def settle_review(db, order_id):
    """处理中断可恢复：扫描状态不一致的订单，按恒等式纠正状态。"""
    order = db.get(TransferOrder, order_id)
    if order is None:
        raise HTTPException(404, '调拨单不存在')
    if order.dispatched_qty != order.received_qty + order.returned_qty + _on_road(order):
        raise HTTPException(500, '订单数量恒等式被破坏，需人工核查')
    if order.status in _SETTLED:
        return order
    if order.status == TransferStatus.in_transit.value and _on_road(order) == 0:
        if order.received_qty > 0 and order.returned_qty > 0:
            order.status = TransferStatus.partially_settled.value
        elif order.received_qty > 0:
            order.status = TransferStatus.completed_received.value
        else:
            order.status = TransferStatus.completed_returned.value
        order.settled_at = time.time()
        db.flush()
    return order


def list_orders(db, status=None, limit=100):
    stmt = select(TransferOrder).order_by(TransferOrder.id.desc()).limit(limit)
    if status:
        stmt = stmt.where(TransferOrder.status == status)
    return list(db.scalars(stmt))


def order_detail(db, order_id):
    order = db.get(TransferOrder, order_id)
    if order is None:
        raise HTTPException(404, '调拨单不存在')
    events = list(db.scalars(
        select(TransferEvent).where(TransferEvent.order_id == order_id)
        .order_by(TransferEvent.id)
    ))
    return order, events

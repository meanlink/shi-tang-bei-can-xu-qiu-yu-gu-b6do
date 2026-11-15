"""厨房备餐台账服务。

库存口径（台账为真相源，stock_balances 为并发安全投影）：
  可用量 = prepared + received - consumed - dispatched + returned
  在途量 = 调拨单 dispatched - received - returned（调出视角/调入视角等量）

所有余额变动经 adjust_balance 的条件原子 UPDATE 完成：
  - 扣减类操作带 available_qty >= delta 守卫，并发下最多一方成功，杜绝超扣；
  - version 乐观锁保证串行化语义在 MySQL/InnoDB 与 SQLite 上都成立。

全局守恒（conservation_check）：
  每道餐 实备总量 = 各档口(可用 + 累计耗用) + 全局在途量。
"""
import time
from fastapi import HTTPException
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from src.models import (
    Stall, Dish, PrepPlan, PrepEntry, Consumption,
    TransferOrder, Job, StockBalance,
)
from src.services.idempotency import lock_rows
from src.constants.enums import PrepStatus


# ---------- 基础资料 ----------
def list_stalls(db, active_only=False):
    stmt = select(Stall).order_by(Stall.id)
    if active_only:
        stmt = stmt.where(Stall.active.is_(True))
    return list(db.scalars(stmt))


def list_dishes(db, active_only=False):
    stmt = select(Dish).order_by(Dish.id)
    if active_only:
        stmt = stmt.where(Dish.active.is_(True))
    return list(db.scalars(stmt))


def _get(db, model, obj_id, label):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f'{label}不存在')
    return obj


# ---------- 库存余额原子操作 ----------
def _available_expr():
    return (
        StockBalance.prepared_qty + StockBalance.received_qty
        - StockBalance.consumed_qty - StockBalance.dispatched_qty
        + StockBalance.returned_qty
    )


def _get_or_create_balance(db, stall_id, dish_id) -> StockBalance:
    bal = db.scalar(
        select(StockBalance).where(
            StockBalance.stall_id == stall_id,
            StockBalance.dish_id == dish_id,
        )
    )
    if bal is not None:
        return bal
    # 并发下另一事务可能已创建同一余额行；用 SAVEPOINT 隔离冲突，
    # 仅回滚保存点而不影响外层事务（如幂等占位记录）。
    try:
        with db.begin_nested():
            bal = StockBalance(
                stall_id=stall_id, dish_id=dish_id,
                prepared_qty=0, consumed_qty=0, dispatched_qty=0,
                received_qty=0, returned_qty=0, version=0,
                updated_at=time.time(),
            )
            db.add(bal)
    except IntegrityError:
        bal = db.scalar(
            select(StockBalance).where(
                StockBalance.stall_id == stall_id,
                StockBalance.dish_id == dish_id,
            )
        )
        if bal is None:
            raise
    return bal


def adjust_balance(db, stall_id, dish_id, changes: dict, require_available: int = 0):
    """对余额做一次条件原子更新。

    changes: prepared_qty/consumed_qty/dispatched_qty/received_qty/returned_qty 的增量。
    require_available>0 时，仅当 当前可用量 >= require_available 才更新，
    否则抛 422（可用量不足/并发冲突，绝不超扣）。
    """
    bal = _get_or_create_balance(db, stall_id, dish_id)
    fields = ('prepared_qty', 'consumed_qty', 'dispatched_qty', 'received_qty', 'returned_qty')
    values = {f: getattr(StockBalance, f) + int(changes.get(f, 0)) for f in fields}
    values['version'] = StockBalance.version + 1
    values['updated_at'] = time.time()
    stmt = update(StockBalance).where(StockBalance.id == bal.id)
    if require_available > 0:
        stmt = stmt.where(_available_expr() >= require_available)
    result = db.execute(stmt.values(**values))
    if result.rowcount == 0:
        # 条件未命中（可用量不足或并发已改动）：本语句未写入任何数据，无需回滚，
        # 直接拒绝，由上层终止请求，保证绝不超扣。
        current = db.scalar(
            select(_available_expr()).where(StockBalance.id == bal.id)
        )
        raise HTTPException(
            422,
            f'可用量不足或存在并发冲突：当前可用 {int(current or 0)} 份，本次需要 {require_available} 份，不能超扣',
        )
    db.refresh(bal)
    return bal


def available_qty(db, stall_id, dish_id) -> int:
    return int(db.scalar(
        select(_available_expr()).where(
            StockBalance.stall_id == stall_id,
            StockBalance.dish_id == dish_id,
        )
    ) or 0)


# ---------- 备餐方案 ----------
def create_plan(db, data, user):
    dish = _get(db, Dish, data.dish_id, '餐品')
    stall = _get(db, Stall, data.stall_id, '档口')
    if data.suggested_qty <= 0:
        raise HTTPException(422, '建议备餐量必须为正整数')
    job = None
    if data.approved_job_id is not None:
        job = db.get(Job, data.approved_job_id)
        if job is None:
            raise HTTPException(404, '关联的分析任务不存在')
        if not (job.status == 'completed' and job.approved):
            raise HTTPException(422, '只能依据已批准的分析建议建立备餐方案')
    exists = db.scalar(
        select(PrepPlan).where(
            PrepPlan.data_date == data.data_date,
            PrepPlan.dish_id == data.dish_id,
            PrepPlan.stall_id == data.stall_id,
        )
    )
    if exists is not None:
        raise HTTPException(409, '该日期/档口/餐品的备餐方案已存在')
    plan = PrepPlan(
        data_date=data.data_date, dish_id=dish.id, stall_id=stall.id,
        suggested_qty=data.suggested_qty, approved_job_id=job.id if job else None,
        status=PrepStatus.pending.value, note=data.note or '',
        created_by=user.id, created_at=time.time(),
    )
    db.add(plan)
    db.flush()
    return plan


def approve_plan(db, plan_id, user):
    plan = _get(db, PrepPlan, plan_id, '备餐方案')
    if plan.status == PrepStatus.approved.value:
        raise HTTPException(409, '方案已批准，请勿重复操作')
    plan.status = PrepStatus.approved.value
    plan.approved_at = time.time()
    db.flush()
    return plan


# ---------- 实备分次登记 ----------
def register_prep(db, data, user):
    plan = _get(db, PrepPlan, data.plan_id, '备餐方案')
    if plan.status != PrepStatus.approved.value:
        raise HTTPException(422, '备餐方案尚未批准，不能登记实备')
    if data.prepared_qty <= 0:
        raise HTTPException(422, '实备量必须为正整数')
    # 并发登记可能算出相同批次号，靠 (plan_id, batch_no) 唯一约束 + SAVEPOINT 重试消解，
    # 回滚仅限保存点，不影响外层事务（如幂等占位记录）。
    entry = None
    for _ in range(3):
        existing = list(db.scalars(
            select(PrepEntry).where(PrepEntry.plan_id == plan.id)
        ))
        batch_no = max((e.batch_no for e in existing), default=0) + 1
        candidate = PrepEntry(
            plan_id=plan.id, batch_no=batch_no, prepared_qty=data.prepared_qty,
            consumed_qty=0, note=data.note or '',
            created_by=user.id, created_at=time.time(),
        )
        try:
            with db.begin_nested():
                db.add(candidate)
            entry = candidate
            break
        except IntegrityError:
            continue
    if entry is None:
        raise HTTPException(409, '实备批次登记冲突，请重试')
    # 实备入库：增加档口可用量
    adjust_balance(db, plan.stall_id, plan.dish_id,
                   {'prepared_qty': data.prepared_qty})
    return entry


# ---------- 领用耗用 ----------
def record_consumption(db, data, user):
    if data.qty <= 0:
        raise HTTPException(422, '领用量必须为正整数')
    entry = lock_rows(db, PrepEntry, [data.entry_id])[0]
    plan = _get(db, PrepPlan, entry.plan_id, '备餐方案')
    batch_remaining = entry.prepared_qty - entry.consumed_qty
    if data.qty > batch_remaining:
        raise HTTPException(422,
            f'领用超量：该批次剩余 {batch_remaining} 份，申请 {data.qty} 份')
    # 原子扣减档口可用量（并发下不超扣）
    adjust_balance(db, plan.stall_id, plan.dish_id,
                   {'consumed_qty': data.qty}, require_available=data.qty)
    entry.consumed_qty += data.qty
    cons = Consumption(
        entry_id=entry.id, stall_id=plan.stall_id, dish_id=plan.dish_id,
        qty=data.qty, purpose=data.purpose or '',
        created_by=user.id, created_at=time.time(),
    )
    db.add(cons)
    db.flush()
    return cons, plan


# ---------- 库存聚合（台账真相源） ----------
def _prep_totals(db):
    rows = db.execute(
        select(PrepPlan.stall_id, PrepPlan.dish_id, func.coalesce(func.sum(PrepEntry.prepared_qty), 0))
        .join(PrepEntry, PrepEntry.plan_id == PrepPlan.id)
        .group_by(PrepPlan.stall_id, PrepPlan.dish_id)
    ).all()
    return {(r[0], r[1]): int(r[2]) for r in rows}


def _consumption_totals(db):
    rows = db.execute(
        select(Consumption.stall_id, Consumption.dish_id, func.coalesce(func.sum(Consumption.qty), 0))
        .group_by(Consumption.stall_id, Consumption.dish_id)
    ).all()
    return {(r[0], r[1]): int(r[2]) for r in rows}


def _transfer_totals(db):
    """返回 (out_in_transit, in_in_transit, dispatched, returned, received) 字典。"""
    out_q, in_q, dispatched, returned, received = {}, {}, {}, {}, {}
    orders = db.scalars(select(TransferOrder)).all()
    for o in orders:
        on_road = o.dispatched_qty - o.received_qty - o.returned_qty
        key_out = (o.from_stall_id, o.dish_id)
        key_in = (o.to_stall_id, o.dish_id)
        dispatched[key_out] = dispatched.get(key_out, 0) + o.dispatched_qty
        returned[key_out] = returned.get(key_out, 0) + o.returned_qty
        received[key_in] = received.get(key_in, 0) + o.received_qty
        if on_road > 0:
            out_q[key_out] = out_q.get(key_out, 0) + on_road
            in_q[key_in] = in_q.get(key_in, 0) + on_road
    return out_q, in_q, dispatched, returned, received


def inventory_view(db):
    """各档口 × 餐品的可用量、在途量、累计耗用、实备总量视图（以余额表为准并附台账值）。"""
    prep = _prep_totals(db)
    consumed = _consumption_totals(db)
    out_road, in_road, dispatched, returned, received = _transfer_totals(db)
    stalls = {s.id: s for s in list_stalls(db)}
    dishes = {d.id: d for d in list_dishes(db)}
    balances = {(b.stall_id, b.dish_id): b for b in db.scalars(select(StockBalance)).all()}
    keys = set(prep) | set(consumed) | set(out_road) | set(in_road) | set(balances)
    view = []
    for stall_id, dish_id in sorted(keys):
        b = balances.get((stall_id, dish_id))
        if b is not None:
            available = (b.prepared_qty + b.received_qty - b.consumed_qty
                         - b.dispatched_qty + b.returned_qty)
            prepared_bal, consumed_bal = b.prepared_qty, b.consumed_qty
        else:
            available = 0
            prepared_bal = consumed_bal = 0
        view.append({
            'stall_id': stall_id,
            'stall_name': stalls.get(stall_id).name if stall_id in stalls else f'#{stall_id}',
            'dish_id': dish_id,
            'dish_name': dishes.get(dish_id).name if dish_id in dishes else f'#{dish_id}',
            'prepared_qty': prepared_bal,
            'ledger_prepared_qty': prep.get((stall_id, dish_id), 0),
            'consumed_qty': consumed_bal,
            'ledger_consumed_qty': consumed.get((stall_id, dish_id), 0),
            'dispatched_qty': dispatched.get((stall_id, dish_id), 0),
            'received_qty': received.get((stall_id, dish_id), 0),
            'returned_qty': returned.get((stall_id, dish_id), 0),
            'out_in_transit_qty': out_road.get((stall_id, dish_id), 0),
            'in_in_transit_qty': in_road.get((stall_id, dish_id), 0),
            'available_qty': available,
        })
    return view


def conservation_check(db):
    """全局守恒校验：每道餐 实备总量 = 各档口(可用+耗用) + 全局在途；并比对余额与台账。"""
    view = inventory_view(db)
    dishes = {}
    for row in view:
        d = dishes.setdefault(row['dish_id'], {
            'dish_id': row['dish_id'], 'dish_name': row['dish_name'],
            'prepared': 0, 'available': 0, 'consumed': 0, 'in_transit': 0,
            'ledger_prepared': 0, 'ledger_consumed': 0, 'ledger_matched': True,
        })
        d['prepared'] += row['prepared_qty']
        d['available'] += row['available_qty']
        d['consumed'] += row['consumed_qty']
        d['in_transit'] += row['in_in_transit_qty']
        d['ledger_prepared'] += row['ledger_prepared_qty']
        d['ledger_consumed'] += row['ledger_consumed_qty']
        if (row['prepared_qty'] != row['ledger_prepared_qty']
                or row['consumed_qty'] != row['ledger_consumed_qty']):
            d['ledger_matched'] = False
    for d in dishes.values():
        quantity_conserved = d['prepared'] == d['available'] + d['consumed'] + d['in_transit']
        d['balanced'] = bool(quantity_conserved and d['ledger_matched'])
    return list(dishes.values())

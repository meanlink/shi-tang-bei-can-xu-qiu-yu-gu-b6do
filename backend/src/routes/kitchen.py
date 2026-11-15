import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import Stall, Dish, PrepPlan, PrepEntry, Consumption, IdempotencyRecord
from src.schemas.kitchen import (
    StallInput, DishInput, PlanInput, PrepEntryInput, ConsumptionInput,
)
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import kitchen
from src.services.audit import record
from src.services.idempotency import idempotent
from src.utils.serialization import (
    stall_dict, dish_dict, plan_dict, entry_dict, consumption_dict,
)

router = APIRouter(prefix='/kitchen')


# ---------- 基础资料 ----------
@router.get('/stalls')
def stalls(db=Depends(get_db), u=Depends(current_user)):
    return [stall_dict(s) for s in kitchen.list_stalls(db)]


@router.post('/stalls', status_code=201)
def create_stall(data: StallInput, db=Depends(get_db), u=Depends(admin_only)):
    if db.scalar(select(Stall).where(Stall.code == data.code)):
        raise HTTPException(409, '档口编码已存在')
    s = Stall(code=data.code, name=data.name, active=True, created_at=time.time())
    db.add(s); db.flush()
    record(db, u.email, 'stall.create', {'id': s.id, 'code': s.code}); db.commit()
    return stall_dict(s)


@router.get('/dishes')
def dishes(db=Depends(get_db), u=Depends(current_user)):
    return [dish_dict(d) for d in kitchen.list_dishes(db)]


@router.post('/dishes', status_code=201)
def create_dish(data: DishInput, db=Depends(get_db), u=Depends(admin_only)):
    if db.scalar(select(Dish).where(Dish.code == data.code)):
        raise HTTPException(409, '餐品编码已存在')
    d = Dish(code=data.code, name=data.name, unit=data.unit, active=True, created_at=time.time())
    db.add(d); db.flush()
    record(db, u.email, 'dish.create', {'id': d.id, 'code': d.code}); db.commit()
    return dish_dict(d)


# ---------- 备餐方案 ----------
@router.get('/plans')
def plans(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(PrepPlan).order_by(PrepPlan.id.desc()).limit(200))
    return [plan_dict(p) for p in rows]


@router.post('/plans', status_code=201)
def create_plan(data: PlanInput, db=Depends(get_db), u=Depends(can_write)):
    p = kitchen.create_plan(db, data, u)
    record(db, u.email, 'prep.plan.create', {'id': p.id, 'date': p.data_date})
    db.commit()
    return plan_dict(p)


@router.post('/plans/{id}/approve')
def approve_plan(id: int, db=Depends(get_db), u=Depends(admin_only)):
    p = kitchen.approve_plan(db, id, u)
    record(db, u.email, 'prep.plan.approve', {'id': id}); db.commit()
    return plan_dict(p)


# ---------- 实备分次登记（幂等） ----------
@router.get('/entries')
def entries(plan_id: int | None = None, db=Depends(get_db), u=Depends(current_user)):
    stmt = select(PrepEntry).order_by(PrepEntry.id.desc()).limit(500)
    if plan_id is not None:
        stmt = select(PrepEntry).where(PrepEntry.plan_id == plan_id).order_by(PrepEntry.id)
    return [entry_dict(e) for e in db.scalars(stmt)]


@router.post('/entries', status_code=201)
def register_entry(data: PrepEntryInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        e = kitchen.register_prep(db, data, u)
        record(db, u.email, 'prep.entry.register',
               {'id': e.id, 'plan_id': e.plan_id, 'qty': e.prepared_qty})
        return entry_dict(e)
    body, replayed = idempotent(db, 'prep.entry', data.idempotency_key, u.email, producer)
    db.commit()
    return body


# ---------- 领用耗用（幂等） ----------
@router.get('/consumptions')
def consumptions(db=Depends(get_db), u=Depends(current_user)):
    rows = db.scalars(select(Consumption).order_by(Consumption.id.desc()).limit(500))
    return [consumption_dict(c) for c in rows]


@router.post('/consumptions', status_code=201)
def consume(data: ConsumptionInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        c, plan = kitchen.record_consumption(db, data, u)
        record(db, u.email, 'prep.consume',
               {'id': c.id, 'entry_id': c.entry_id, 'qty': c.qty, 'dish_id': plan.dish_id})
        return consumption_dict(c)
    body, replayed = idempotent(db, 'consumption', data.idempotency_key, u.email, producer)
    db.commit()
    return body


# ---------- 库存与守恒 ----------
@router.get('/inventory')
def inventory(db=Depends(get_db), u=Depends(current_user)):
    return kitchen.inventory_view(db)


@router.get('/conservation')
def conservation(db=Depends(get_db), u=Depends(current_user)):
    return kitchen.conservation_check(db)


# ---------- 中断恢复：清理遗留的幂等处理占位 ----------
@router.post('/idempotency/recover')
def recover_idempotency(scope: str, key: str, db=Depends(get_db), u=Depends(can_write)):
    """处理中断后，释放仍处于 processing 的幂等占位，使同键请求可重试。

    仅清理未完成占位；已完成记录受唯一约束保护，不会被删除，回放语义不变。
    """
    rec = db.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.scope == scope,
        IdempotencyRecord.idempotency_key == key,
    ))
    if rec is None:
        raise HTTPException(404, '未找到对应请求记录')
    if rec.status == 'completed':
        raise HTTPException(409, '该请求已完成，无需恢复，重试将返回首次结果')
    db.delete(rec)
    record(db, u.email, 'idempotency.recover', {'scope': scope, 'key': key})
    db.commit()
    return {'recovered': True, 'scope': scope, 'idempotency_key': key}

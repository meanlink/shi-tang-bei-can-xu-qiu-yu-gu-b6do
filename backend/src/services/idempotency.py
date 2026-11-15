"""幂等与并发控制工具。

- idempotent: 以 (scope, key) 唯一约束保证同一业务请求只生效一次；
  响应丢失后客户端带同一 key 重试，直接返回首次结果，不重复记账。
  记录有 processing/completed 两个状态：
    * 首次请求先置 processing，业务执行成功后置 completed 并保存响应；
    * 执行抛错则整体回滚（唯一键随事务一起撤销），客户端可用同键安全重试；
    * 处理中断留下 processing 记录时，重试判定为超时占用，提示稍后再试，
      由人工或 recover 流程清理，绝不静默重复记账。
- lock_rows: 跨方言行锁（MySQL/InnoDB 用 SELECT ... FOR UPDATE，SQLite/测试库退化为普通查询，
  依靠条件原子 UPDATE 与唯一约束兜底），辅助调拨状态机串行化。
"""
import time
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.models import IdempotencyRecord


def _dialect_name(db) -> str:
    return db.bind.dialect.name if db.bind else ''


def lock_rows(db, model, keys):
    """按主键加行锁并返回实体列表，按 id 排序避免多锁死锁。"""
    ids = sorted(k for k in keys if k is not None)
    if not ids:
        return []
    stmt = select(model).where(model.id.in_(ids)).order_by(model.id)
    if _dialect_name(db) == 'mysql':
        stmt = stmt.with_for_update()
    rows = list(db.scalars(stmt))
    found = {r.id for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(404, f'{model.__tablename__} 记录不存在: {missing}')
    return rows


class ReplayedRequest(Exception):
    """命中已完成的幂等记录，由路由层转换为首次响应。"""
    def __init__(self, body):
        self.body = body


def idempotent(db, scope: str, key: str | None, actor: str, producer):
    """包装一个产生副作用的业务函数 producer() -> dict。

    key 为空时不启用幂等（每次请求独立执行）。
    返回 (body, replayed)；命中已完成历史记录时 replayed=True。
    """
    if not key:
        return producer(), False

    existing = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.status == 'completed':
            return existing.response_body, True
        # processing：上次请求仍在执行或已中断，不能重复记账
        raise HTTPException(409, '相同请求仍在处理或上次处理中断，请稍后重试或执行恢复')

    record = IdempotencyRecord(
        scope=scope, idempotency_key=key, status='processing',
        response_body={}, actor=actor, created_at=time.time(),
    )
    db.add(record)
    try:
        db.flush()  # 抢占唯一键，并发重复请求在此 IntegrityError 失败
    except IntegrityError:
        db.rollback()
        other = db.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if other is not None and other.status == 'completed':
            return other.response_body, True
        raise HTTPException(409, '相同请求正在处理中，请稍后重试')

    try:
        body = producer()
    except Exception:
        # 业务失败：回滚整个事务，processing 占位一并撤销，客户端可安全重试
        db.rollback()
        raise
    record.status = 'completed'
    record.response_body = body
    db.flush()
    return body, False

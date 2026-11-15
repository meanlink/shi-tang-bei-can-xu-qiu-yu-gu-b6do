from sqlalchemy import String, Integer, Double, ForeignKey, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Stall(Base):
    """档口：餐品在厨房内的持有单位。"""
    __tablename__ = 'stalls'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Double)


class Dish(Base):
    """餐品：备餐与调拨的最小品类。"""
    __tablename__ = 'dishes'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(16), default='份')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Double)


class PrepPlan(Base):
    """备餐方案（每日按已批准建议建立）。

    data_date 为业务日期（YYYY-MM-DD）；suggested_qty 来自已批准的分析建议，
    approved_job_id 关联批准的分析任务，方案本身经 admin 批准后才可登记实备。
    """
    __tablename__ = 'prep_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    data_date: Mapped[str] = mapped_column(String(10), index=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    stall_id: Mapped[int] = mapped_column(ForeignKey('stalls.id'))
    suggested_qty: Mapped[int]
    approved_job_id: Mapped[int | None] = mapped_column(ForeignKey('jobs.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')  # pending/approved
    note: Mapped[str] = mapped_column(String(300), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    approved_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    __table_args__ = (UniqueConstraint('data_date', 'dish_id', 'stall_id', name='uq_prep_plan_scope'),)


class PrepEntry(Base):
    """备餐台账：实备分次登记，并记录领用耗用。

    prepared_qty 为本批次实备入库量；consumed_qty 为该批次累计领用耗用。
    实备量进入所属档口可用量，耗用量从可用量中扣减。
    """
    __tablename__ = 'prep_entries'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('prep_plans.id'), index=True)
    batch_no: Mapped[int] = mapped_column(Integer, default=1)
    prepared_qty: Mapped[int]
    consumed_qty: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(300), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('plan_id', 'batch_no', name='uq_prep_entry_batch'),)


class Consumption(Base):
    """领用耗用流水（与 prep_entry 余额联动，保留逐笔追溯）。"""
    __tablename__ = 'consumptions'
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey('prep_entries.id'), index=True)
    stall_id: Mapped[int] = mapped_column(ForeignKey('stalls.id'))
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    qty: Mapped[int]
    purpose: Mapped[str] = mapped_column(String(160), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class TransferOrder(Base):
    """跨档口调拨单。

    状态机：pending（待发）→ in_transit（在途）→ completed（结清）；
    任一步可出现 returned / partially_returned 的退回结果。
    数量口径：
      dispatched_qty 已调出（在途口径，受可用量行锁约束，不可超扣）；
      received_qty   接收档口累计确认接收；
      returned_qty   退回原档口累计；
      恒等式 dispatched_qty = received_qty + returned_qty + 在途余量。
    """
    __tablename__ = 'transfer_orders'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(40), unique=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey('prep_plans.id'), nullable=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    from_stall_id: Mapped[int] = mapped_column(ForeignKey('stalls.id'))
    to_stall_id: Mapped[int] = mapped_column(ForeignKey('stalls.id'))
    request_qty: Mapped[int]
    dispatched_qty: Mapped[int] = mapped_column(Integer, default=0)
    received_qty: Mapped[int] = mapped_column(Integer, default=0)
    returned_qty: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    # pending / in_transit / completed_received / completed_returned / partially_settled
    note: Mapped[str] = mapped_column(String(300), default='')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    dispatched_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    settled_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class TransferEvent(Base):
    """调拨逐笔事件：页面追查待发→在途→部分接收→结清的每次数量变化。"""
    __tablename__ = 'transfer_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('transfer_orders.id'), index=True)
    event_type: Mapped[str] = mapped_column(String(24))
    # dispatch / receive / return / settle
    qty: Mapped[int] = mapped_column(Integer, default=0)
    from_status: Mapped[str] = mapped_column(String(24), default='')
    to_status: Mapped[str] = mapped_column(String(24), default='')
    actor: Mapped[str] = mapped_column(String(150))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Double)


class IdempotencyRecord(Base):
    """幂等记录：响应丢失后重试不重复记账。

    以 (scope, idempotency_key) 唯一约束保证同一业务请求只生效一次，
    响应体落库，重试直接返回首次结果。
    """
    __tablename__ = 'idempotency_records'
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(48), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    # processing=业务执行中（含中断占位）；completed=已完成，重试直接回放响应
    status: Mapped[str] = mapped_column(String(16), default='processing', index=True)
    response_body: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(150), default='')
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('scope', 'idempotency_key', name='uq_idempotency_scope_key'),)


class StockBalance(Base):
    """档口 × 餐品库存余额（并发安全的投影表）。

    可用量 = prepared + received - consumed - dispatched + returned
    所有变动均通过带可用量条件的原子 UPDATE 完成，配合 version 乐观锁，
    保证并发调出/领用同一份餐不会超扣（MySQL 与 SQLite 语义均成立）。
    台账（prep_entries/consumptions/transfer_orders/events）为追溯真相源，
    conservation_check 双向比对余额与台账聚合。
    """
    __tablename__ = 'stock_balances'
    id: Mapped[int] = mapped_column(primary_key=True)
    stall_id: Mapped[int] = mapped_column(ForeignKey('stalls.id'))
    dish_id: Mapped[int] = mapped_column(ForeignKey('dishes.id'))
    prepared_qty: Mapped[int] = mapped_column(Integer, default=0)
    consumed_qty: Mapped[int] = mapped_column(Integer, default=0)
    dispatched_qty: Mapped[int] = mapped_column(Integer, default=0)
    received_qty: Mapped[int] = mapped_column(Integer, default=0)
    returned_qty: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('stall_id', 'dish_id', name='uq_stock_balance_scope'),)


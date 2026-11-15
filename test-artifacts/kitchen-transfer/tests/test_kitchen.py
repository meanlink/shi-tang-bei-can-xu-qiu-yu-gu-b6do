"""厨房备餐与跨档口调拨模块的独立验收测试。

运行（在 backend 同级）：
  python -m pytest test-artifacts/kitchen-transfer/tests -q
使用 SQLite 内存库验证业务规则；并发超扣依赖条件原子 UPDATE，
该语义在 MySQL/InnoDB 与 SQLite 上均成立。
"""
import os
import sys
import tempfile
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'backend'))
sys.path.insert(0, BACKEND)

# 文件型 SQLite：TestClient 请求线程与并发工作线程各自获得独立连接，
# 条件原子 UPDATE 的超扣守卫在写锁下依然成立。
_DB_FD, _DB_PATH = tempfile.mkstemp(prefix='kitchen_test_', suffix='.db')
os.close(_DB_FD)
os.unlink(_DB_PATH)
os.environ.setdefault('DATABASE_URL', f'sqlite:///{_DB_PATH}')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')

from src.config.database import Base, get_db  # noqa: E402
from src.models import (  # noqa: E402
    User, Stall, Dish, PrepPlan, PrepEntry, Consumption,
    TransferOrder, TransferEvent, IdempotencyRecord, StockBalance,
)
from src.middlewares.auth import current_user, can_write, admin_only  # noqa: E402
from src.routes import kitchen as kitchen_routes  # noqa: E402
from src.routes import transfers as transfer_routes  # noqa: E402

engine = create_engine(
    f'sqlite:///{_DB_PATH}',
    connect_args={'check_same_thread': False, 'timeout': 10},
)
# 并发写入场景下让 SQLite 等待写锁而非立即报错
@event.listens_for(engine, 'connect')
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute('PRAGMA busy_timeout=10000')
    cur.close()

TestingSession = sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(scope='session', autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    """每个用例清空并重建基础数据。"""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = TestingSession()
    import time
    users = {
        'admin': User(email='admin@t.local', name='负责人', role='admin', password='x'),
        'analyst': User(email='analyst@t.local', name='分析员', role='analyst', password='x'),
        'viewer': User(email='viewer@t.local', name='观察员', role='viewer', password='x'),
    }
    session.add_all(users.values())
    session.flush()
    now = time.time()
    stalls = [
        Stall(code='A', name='热炒档口', active=True, created_at=now),
        Stall(code='B', name='面点档口', active=True, created_at=now),
        Stall(code='C', name='粉面档口', active=True, created_at=now),
    ]
    dishes = [
        Dish(code='D1', name='红烧肉', unit='份', active=True, created_at=now),
        Dish(code='D2', name='时蔬', unit='份', active=True, created_at=now),
    ]
    session.add_all(stalls + dishes)
    session.commit()
    session.close()
    yield


@pytest.fixture()
def user_map(db):
    with TestingSession() as s:
        return {u.role: u for u in s.scalars(select(User))}


@pytest.fixture()
def client(user_map):
    from types import SimpleNamespace
    app = FastAPI()
    app.include_router(kitchen_routes.router)
    app.include_router(transfer_routes.router)

    def _override(role):
        u = user_map[role]
        holder = SimpleNamespace(id=u.id, email=u.email, name=u.name, role=u.role)

        def _f():
            return holder
        return _f

    app.dependency_overrides[current_user] = _override('analyst')
    app.dependency_overrides[can_write] = _override('analyst')
    app.dependency_overrides[admin_only] = _override('admin')

    def _get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app)


# ---------- 服务层直接测试 ----------
def _make_plan(session, user, dish_id, stall_id, qty=100, date='2026-11-15'):
    from src.services.kitchen import create_plan, approve_plan
    from src.schemas.kitchen import PlanInput

    class U:
        id = user.id
        email = user.email
        role = user.role
    plan = create_plan(session, PlanInput(
        data_date=date, dish_id=dish_id, stall_id=stall_id,
        suggested_qty=qty,
    ), U())
    approve_plan(session, plan.id, U())
    session.commit()
    return plan.id


def _prep(session, user, plan_id, qty, key=None):
    from src.services.kitchen import register_prep
    from src.schemas.kitchen import PrepEntryInput

    class U:
        id = user.id
        email = user.email
        role = user.role
    e = register_prep(session, PrepEntryInput(
        plan_id=plan_id, prepared_qty=qty, idempotency_key=key,
    ), U())
    session.commit()
    return e.id


def test_full_lifecycle_and_conservation(db, user_map):
    from src.services import kitchen, transfers
    from src.schemas.kitchen import (
        PrepEntryInput, TransferOrderInput, TransferActionInput, ConsumptionInput,
    )
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        stall_a = s.scalar(select(Stall).where(Stall.code == 'A'))
        stall_b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, stall_a.id, qty=100)
        # 实备分次登记：60 + 40
        e1 = _prep(s, admin, plan_id, 60)
        e2 = _prep(s, admin, plan_id, 40)
        assert s.get(PrepEntry, e1).batch_no == 1
        assert s.get(PrepEntry, e2).batch_no == 2
        inv = {r['stall_id']: r for r in kitchen.inventory_view(s)}
        assert inv[stall_a.id]['available_qty'] == 100
        assert inv[stall_a.id]['prepared_qty'] == 100

        # 领用耗用 10
        kitchen.record_consumption(s, ConsumptionInput(
            entry_id=e1, qty=10, purpose='午餐核销'), U())
        s.commit()
        assert kitchen.available_qty(s, stall_a.id, dish.id) == 90

        # 调拨 A -> B 申请 50，调出 50 进入在途
        order = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=stall_a.id, to_stall_id=stall_b.id,
            request_qty=50, plan_id=plan_id,
        ), U())
        s.commit()
        transfers.dispatch(s, order.id, 50, admin.email)
        s.commit()
        # 调出后 A 可用 40，B 可用仍为 0（在途不计入接收方可用）
        assert kitchen.available_qty(s, stall_a.id, dish.id) == 40
        assert kitchen.available_qty(s, stall_b.id, dish.id) == 0

        # 部分接收 30
        transfers.receive(s, order.id, 30, admin.email)
        s.commit()
        o = s.get(TransferOrder, order.id)
        assert o.status == 'in_transit'
        assert o.received_qty == 30
        assert kitchen.available_qty(s, stall_b.id, dish.id) == 30

        # 余量 20 退回原档口 A
        transfers.return_order(s, order.id, 20, admin.email)
        s.commit()
        o = s.get(TransferOrder, order.id)
        assert o.status == 'partially_settled'
        assert (o.dispatched_qty, o.received_qty, o.returned_qty) == (50, 30, 20)
        # A: 100 - 10耗用 - 50调出 + 20退回 = 60；B: 30；在途 0；总实备 100
        assert kitchen.available_qty(s, stall_a.id, dish.id) == 60
        assert kitchen.available_qty(s, stall_b.id, dish.id) == 30

        check = {c['dish_id']: c for c in kitchen.conservation_check(s)}
        c = check[dish.id]
        assert c['balanced'] is True
        assert c['prepared'] == 100
        assert c['available'] == 90
        assert c['consumed'] == 10
        assert c['in_transit'] == 0
        assert c['ledger_matched'] is True


def test_over_deduction_rejected(db, user_map):
    from src.services import kitchen, transfers
    from src.schemas.kitchen import TransferOrderInput, TransferActionInput
    from fastapi import HTTPException
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=30)
        _prep(s, admin, plan_id, 30)
        o1 = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id, request_qty=20), U())
        o2 = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id, request_qty=20), U())
        s.commit()
        transfers.dispatch(s, o1.id, 20, admin.email)
        s.commit()
        # 仅剩 10，第二单调出 20 必须拒绝，不能超扣
        with pytest.raises(HTTPException) as ei:
            transfers.dispatch(s, o2.id, 20, admin.email)
        assert ei.value.status_code == 422
        s.rollback()
        assert kitchen.available_qty(s, a.id, dish.id) == 10
        # 余额表没有被扣成负数
        bal = s.scalar(select(StockBalance).where(
            StockBalance.stall_id == a.id, StockBalance.dish_id == dish.id))
        assert bal.prepared_qty == 30 and bal.dispatched_qty == 20


def test_concurrent_dispatch_only_one_succeeds(db, user_map):
    """并发调出同一份餐：条件原子 UPDATE 保证最多一笔成功，绝不超扣。"""
    from src.services import transfers
    from src.schemas.kitchen import TransferOrderInput
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=50)
        _prep(s, admin, plan_id, 50)
        ids = []
        for _ in range(2):
            o = transfers.create_order(s, TransferOrderInput(
                dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id, request_qty=50), U())
            ids.append(o.id)
        s.commit()

    outcomes = []

    def worker(oid):
        try:
            with TestingSession() as ws:
                transfers.dispatch(ws, oid, 50, admin.email)
                ws.commit()
                outcomes.append(('ok', oid))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(('fail', getattr(exc, 'status_code', str(exc))))

    t1 = threading.Thread(target=worker, args=(ids[0],))
    t2 = threading.Thread(target=worker, args=(ids[1],))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert len(outcomes) == 2
    assert sorted(o[0] for o in outcomes) == ['fail', 'ok']
    with TestingSession() as s:
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        from src.services import kitchen
        assert kitchen.available_qty(s, a.id, dish.id) == 0
        dispatched = s.scalar(
            select(StockBalance.dispatched_qty).where(
                StockBalance.stall_id == a.id, StockBalance.dish_id == dish.id))
        assert dispatched == 50  # 没有被扣成 100


def test_receive_and_return_are_mutually_exclusive_settlement(db, user_map):
    """全量接收与全量退回互斥；部分接收后只退余量。"""
    from src.services import transfers
    from src.schemas.kitchen import TransferOrderInput
    from fastapi import HTTPException
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D2'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        c = s.scalar(select(Stall).where(Stall.code == 'C'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=40)
        _prep(s, admin, plan_id, 40)
        o = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=c.id, request_qty=40), U())
        s.commit()
        transfers.dispatch(s, o.id, 40, admin.email)
        s.commit()
        # 全量接收后结清，退回必须被拒绝（只认一方）
        transfers.receive(s, o.id, 40, admin.email)
        s.commit()
        assert s.get(TransferOrder, o.id).status == 'completed_received'
        with pytest.raises(HTTPException) as ei:
            transfers.return_order(s, o.id, 1, admin.email)
        assert ei.value.status_code == 409


def test_full_return_restores_source_stall(db, user_map):
    from src.services import kitchen, transfers
    from src.schemas.kitchen import TransferOrderInput
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D2'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=25)
        _prep(s, admin, plan_id, 25)
        o = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id, request_qty=25), U())
        s.commit()
        transfers.dispatch(s, o.id, 25, admin.email); s.commit()
        assert kitchen.available_qty(s, a.id, dish.id) == 0
        transfers.return_order(s, o.id, 25, admin.email); s.commit()
        assert s.get(TransferOrder, o.id).status == 'completed_returned'
        # 退回回到原档口
        assert kitchen.available_qty(s, a.id, dish.id) == 25
        assert kitchen.available_qty(s, b.id, dish.id) == 0
        check = kitchen.conservation_check(s)[0]
        assert check['balanced'] and check['in_transit'] == 0


def test_idempotent_retry_does_not_double_book(db, user_map):
    """响应丢失后重试：同一幂等键只生效一次。"""
    from src.services import kitchen
    from src.schemas.kitchen import PrepEntryInput, ConsumptionInput
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=100)
        from src.services.idempotency import idempotent
        payload = PrepEntryInput(plan_id=plan_id, prepared_qty=15, idempotency_key='k-1')

        def producer():
            e = kitchen.register_prep(s, payload, U())
            return {'id': e.id, 'qty': e.prepared_qty}

        body1, replayed1 = idempotent(s, 'prep.entry', 'k-1', admin.email, producer)
        s.commit()
        # 模拟客户端重试：再次用同一键
        body2, replayed2 = idempotent(s, 'prep.entry', 'k-1', admin.email, producer)
        s.commit()
        assert replayed1 is False and replayed2 is True
        assert body1 == body2
        # 只登记了一批 15，而非 30
        entries = s.scalars(select(PrepEntry).where(PrepEntry.plan_id == plan_id)).all()
        assert [(e.batch_no, e.prepared_qty) for e in entries] == [(1, 15)]

        # 领用耗用同样幂等
        eid = entries[0].id
        c_payload = ConsumptionInput(entry_id=eid, qty=5, idempotency_key='c-1')

        def c_producer():
            c, _ = kitchen.record_consumption(s, c_payload, U())
            return {'id': c.id}
        b1, r1 = idempotent(s, 'consumption', 'c-1', admin.email, c_producer); s.commit()
        b2, r2 = idempotent(s, 'consumption', 'c-1', admin.email, c_producer); s.commit()
        assert r1 is False and r2 is True and b1 == b2
        assert s.get(PrepEntry, eid).consumed_qty == 5
        assert s.scalar(select(StockBalance.consumed_qty).where(
            StockBalance.stall_id == a.id, StockBalance.dish_id == dish.id)) == 5


def test_recover_after_interruption(db, user_map):
    """处理中断可恢复：在途量已清零但状态未结清时，按恒等式纠正。"""
    from src.services import transfers
    from src.schemas.kitchen import TransferOrderInput
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=20)
        _prep(s, admin, plan_id, 20)
        o = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id, request_qty=20), U())
        s.commit()
        transfers.dispatch(s, o.id, 20, admin.email); s.commit()
        transfers.receive(s, o.id, 12, admin.email); s.commit()
        transfers.return_order(s, o.id, 8, admin.email); s.commit()
        # 模拟处理中断：数量恒等式已结清，但状态被遗留在“在途”
        order = s.get(TransferOrder, o.id)
        assert order.status == 'partially_settled'
        order.status = 'in_transit'
        order.settled_at = None
        s.commit()
        # recover 按恒等式恢复结清状态
        recovered = transfers.settle_review(s, o.id)
        s.commit()
        assert recovered.status == 'partially_settled'
        assert recovered.settled_at is not None
        assert recovered.dispatched_qty == recovered.received_qty + recovered.returned_qty


def test_event_trail_covers_all_quantity_changes(db, user_map):
    """页面追查：每笔调拨有待发→在途→部分接收→结清的事件流水。"""
    from src.services import transfers
    from src.schemas.kitchen import TransferOrderInput
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        b = s.scalar(select(Stall).where(Stall.code == 'B'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=60)
        _prep(s, admin, plan_id, 60)
        o = transfers.create_order(s, TransferOrderInput(
            dish_id=dish.id, from_stall_id=a.id, to_stall_id=b.id,
            request_qty=60, plan_id=plan_id), U())
        s.commit()
        transfers.dispatch(s, o.id, 60, admin.email); s.commit()
        transfers.receive(s, o.id, 40, admin.email); s.commit()
        transfers.return_order(s, o.id, 20, admin.email); s.commit()
        order, events = transfers.order_detail(s, o.id)
        types = [e.event_type for e in events]
        assert 'dispatch' in types and 'receive' in types
        assert 'return' in types and 'settle' in types
        # 关联备餐方案可追查
        assert order.plan_id == plan_id
        receive_ev = next(e for e in events if e.event_type == 'receive')
        assert receive_ev.from_status == 'in_transit'
        settle_ev = next(e for e in events if e.event_type == 'settle' and e.detail.get('reason'))
        assert settle_ev.detail['reason'] == 'partially_settled'


def test_consumption_cannot_overuse_batch(db, user_map):
    from src.services.kitchen import record_consumption
    from src.schemas.kitchen import ConsumptionInput
    from fastapi import HTTPException
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        plan_id = _make_plan(s, admin, dish.id, a.id, qty=10)
        eid = _prep(s, admin, plan_id, 10)
        with pytest.raises(HTTPException) as ei:
            record_consumption(s, ConsumptionInput(entry_id=eid, qty=11), U())
        assert ei.value.status_code == 422


def test_duplicate_plan_rejected(db, user_map):
    from src.services.kitchen import create_plan
    from src.schemas.kitchen import PlanInput
    from fastapi import HTTPException
    admin = user_map['admin']

    class U:
        id = admin.id
        email = admin.email
        role = 'admin'

    with TestingSession() as s:
        dish = s.scalar(select(Dish).where(Dish.code == 'D1'))
        a = s.scalar(select(Stall).where(Stall.code == 'A'))
        data = PlanInput(data_date='2026-11-15', dish_id=dish.id,
                         stall_id=a.id, suggested_qty=10)
        create_plan(s, data, U()); s.commit()
        with pytest.raises(HTTPException) as ei:
            create_plan(s, data, U())
        assert ei.value.status_code == 409


def test_idempotency_three_state_lifecycle(db, user_map):
    """三态幂等：失败回滚可重试；中断占位拒绝重复；成功后回放。"""
    from src.services.idempotency import idempotent
    from src.models import IdempotencyRecord
    from fastapi import HTTPException
    admin = user_map['admin']

    # 1) 业务抛错时整体回滚，同一幂等键可安全重试且只生效一次
    with TestingSession() as s:
        calls = {'n': 0}

        def flaky():
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('boom')
            return {'ok': True}
        with pytest.raises(RuntimeError):
            idempotent(s, 'scope.x', 'k-fail', admin.email, flaky)
        s.rollback()
        # 失败后无占位记录残留
        assert s.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == 'k-fail')) is None
        body, replayed = idempotent(s, 'scope.x', 'k-fail', admin.email, flaky)
        s.commit()
        assert body == {'ok': True} and replayed is False
        assert calls['n'] == 2  # 第二次才真正执行

    # 2) 中断遗留 processing 占位时，重试被拒绝，不重复记账
    with TestingSession() as s:
        s.add(IdempotencyRecord(scope='scope.y', idempotency_key='k-hang',
                                status='processing', response_body={},
                                actor=admin.email, created_at=0))
        s.commit()
        with pytest.raises(HTTPException) as ei:
            idempotent(s, 'scope.y', 'k-hang', admin.email, lambda: {'x': 1})
        assert ei.value.status_code == 409

    # 3) 已完成记录直接回放，producer 不再执行
    with TestingSession() as s:
        ran = {'n': 0}
        body, replayed = idempotent(
            s, 'scope.x', 'k-fail', admin.email,
            lambda: (ran.__setitem__('n', ran['n'] + 1), {'fresh': True})[1])
        assert replayed is True and body == {'ok': True} and ran['n'] == 0


def test_concurrent_idempotent_request_single_effect(db, user_map):
    """并发同一幂等键：唯一约束保证只有一个请求生效。"""
    from src.services.idempotency import idempotent
    from src.models import IdempotencyRecord
    admin = user_map['admin']
    errors, done = [], []

    def worker():
        try:
            with TestingSession() as ws:
                body, replayed = idempotent(
                    ws, 'scope.c', 'same-key', admin.email, lambda: {'v': 1})
                ws.commit()
                done.append(replayed)
        except Exception as exc:  # noqa: BLE001
            errors.append(getattr(exc, 'status_code', str(exc)))

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()
    with TestingSession() as s:
        rows = s.scalars(select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == 'same-key')).all()
        assert len(rows) == 1 and rows[0].status == 'completed'
    # 一个真正执行（replayed=False），另一个要么回放要么冲突，绝不两次生效
    assert len(done) + len(errors) == 2


# ---------- HTTP 层测试（含鉴权与端到端幂等） ----------
def test_http_full_flow_and_idempotency(client):
    # 基础资料
    r = client.get('/kitchen/stalls')
    assert r.status_code == 200 and len(r.json()) == 3
    r = client.get('/kitchen/dishes')
    dishes = r.json()
    stalls = client.get('/kitchen/stalls').json()
    d1 = next(d for d in dishes if d['code'] == 'D1')
    a = next(s for s in stalls if s['code'] == 'A')
    b = next(s for s in stalls if s['code'] == 'B')

    # 建方案 -> 批准（admin override 生效）
    plan = client.post('/kitchen/plans', json={
        'data_date': '2026-11-15', 'dish_id': d1['id'],
        'stall_id': a['id'], 'suggested_qty': 80,
    }).json()
    approved = client.post(f"/kitchen/plans/{plan['id']}/approve")
    assert approved.status_code == 200 and approved.json()['status'] == 'approved'

    # 实备登记带幂等键，重复提交只入库一次
    payload = {'plan_id': plan['id'], 'prepared_qty': 80, 'idempotency_key': 'http-k-1'}
    r1 = client.post('/kitchen/entries', json=payload)
    r2 = client.post('/kitchen/entries', json=payload)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json() == r2.json()
    entries = client.get(f"/kitchen/entries?plan_id={plan['id']}").json()
    assert len(entries) == 1 and entries[0]['prepared_qty'] == 80

    # 调拨全流程
    order = client.post('/transfers', json={
        'dish_id': d1['id'], 'from_stall_id': a['id'], 'to_stall_id': b['id'],
        'request_qty': 80, 'plan_id': plan['id'], 'idempotency_key': 'http-t-1',
    }).json()
    # 重试创建不重复
    again = client.post('/transfers', json={
        'dish_id': d1['id'], 'from_stall_id': a['id'], 'to_stall_id': b['id'],
        'request_qty': 80, 'plan_id': plan['id'], 'idempotency_key': 'http-t-1',
    })
    assert again.json()['id'] == order['id']
    assert client.get('/transfers').json().__len__() == 1

    d = client.post(f"/transfers/{order['id']}/dispatch",
                    json={'qty': 80, 'idempotency_key': 'http-d-1'})
    assert d.status_code == 200 and d.json()['status'] == 'in_transit'
    # 调出重试不重复记账
    d2 = client.post(f"/transfers/{order['id']}/dispatch",
                     json={'qty': 80, 'idempotency_key': 'http-d-1'})
    assert d2.json()['id'] == order['id']

    r = client.post(f"/transfers/{order['id']}/receive", json={'qty': 50})
    assert r.json()['status'] == 'in_transit'
    r = client.post(f"/transfers/{order['id']}/return", json={'qty': 30})
    assert r.json()['status'] == 'partially_settled'

    detail = client.get(f"/transfers/{order['id']}").json()
    assert detail['order']['order_no'].startswith('TR')
    assert len(detail['events']) >= 4

    inv = {x['stall_id']: x for x in client.get('/kitchen/inventory').json()}
    assert inv[a['id']]['available_qty'] == 30   # 80 - 80 + 30 退回
    assert inv[b['id']]['available_qty'] == 50
    conservation = client.get('/kitchen/conservation').json()
    assert all(c['balanced'] for c in conservation)


def test_http_validation_errors(client):
    stalls = client.get('/kitchen/stalls').json()
    dishes = client.get('/kitchen/dishes').json()
    # 非法日期
    r = client.post('/kitchen/plans', json={
        'data_date': '15-11-2026', 'dish_id': dishes[0]['id'],
        'stall_id': stalls[0]['id'], 'suggested_qty': 10,
    })
    assert r.status_code == 422
    # 同档口调拨
    r = client.post('/transfers', json={
        'dish_id': dishes[0]['id'],
        'from_stall_id': stalls[0]['id'], 'to_stall_id': stalls[0]['id'],
        'request_qty': 5,
    })
    assert r.status_code == 422


def test_http_idempotency_recover_endpoint(client):
    """中断恢复接口：清理 processing 占位，completed 记录受保护。"""
    from src.models import IdempotencyRecord
    import time
    # 直接插入一个 processing 占位
    with TestingSession() as s:
        s.add(IdempotencyRecord(scope='transfer.dispatch', idempotency_key='stuck-1',
                                status='processing', response_body={},
                                actor='a@t', created_at=time.time()))
        s.commit()
    r = client.post('/kitchen/idempotency/recover?scope=transfer.dispatch&key=stuck-1')
    assert r.status_code == 200 and r.json()['recovered'] is True
    with TestingSession() as s:
        assert s.scalar(select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == 'stuck-1')) is None
    # 不存在的记录
    r = client.post('/kitchen/idempotency/recover?scope=x&key=missing')
    assert r.status_code == 404

import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import (
    User, Dataset, Job, Report, Delivery, Audit,
    Stall, Dish, PrepPlan, PrepEntry, TransferOrder, TransferEvent, StockBalance,
)
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.services import kitchen, transfers
from src.schemas.kitchen import (
    PlanInput, PrepEntryInput, TransferOrderInput, TransferActionInput,
)
from src.utils.security import hash_password
from src.utils.serialization import checksum


class _Actor:
    """服务层需要的最小操作者结构。"""
    def __init__(self, user):
        self.id = user.id
        self.email = user.email
        self.role = user.role


def _seed_kitchen(db, admin, approved_job):
    """幂等写入厨房基础资料与一条备餐/调拨样例，不覆盖用户修改。"""
    stall_defs = [('ST-HOT', '热炒档口'), ('ST-FLOUR', '面点档口'), ('ST-NOODLE', '粉面档口')]
    dish_defs = [('D-PORK', '红烧肉', '份'), ('D-VEG', '时令时蔬', '份'), ('D-RICE', '杂粮饭', '份')]
    stalls = {}
    for code, name in stall_defs:
        s = db.scalar(select(Stall).where(Stall.code == code))
        if s is None:
            s = Stall(code=code, name=name, active=True, created_at=time.time())
            db.add(s); db.flush()
        stalls[code] = s
    dishes = {}
    for code, name, unit in dish_defs:
        d = db.scalar(select(Dish).where(Dish.code == code))
        if d is None:
            d = Dish(code=code, name=name, unit=unit, active=True, created_at=time.time())
            db.add(d); db.flush()
        dishes[code] = d
    actor = _Actor(admin)
    data_date = time.strftime('%Y-%m-%d')
    plan = db.scalar(select(PrepPlan).where(
        PrepPlan.data_date == data_date,
        PrepPlan.dish_id == dishes['D-PORK'].id,
        PrepPlan.stall_id == stalls['ST-HOT'].id,
    ))
    if plan is None:
        plan = kitchen.create_plan(db, PlanInput(
            data_date=data_date, dish_id=dishes['D-PORK'].id,
            stall_id=stalls['ST-HOT'].id, suggested_qty=120,
            approved_job_id=approved_job.id, note='演示：按已批准建议建立',
        ), actor)
        kitchen.approve_plan(db, plan.id, actor)
        kitchen.register_prep(db, PrepEntryInput(
            plan_id=plan.id, prepared_qty=80, note='首批实备'), actor)
        kitchen.register_prep(db, PrepEntryInput(
            plan_id=plan.id, prepared_qty=40, note='补批次实备'), actor)
        db.flush()
        # 一笔演示调拨：热炒 -> 面点，调出 50、部分接收 35、余量 15 退回
        order = transfers.create_order(db, TransferOrderInput(
            dish_id=dishes['D-PORK'].id, from_stall_id=stalls['ST-HOT'].id,
            to_stall_id=stalls['ST-FLOUR'].id, request_qty=50,
            plan_id=plan.id, note='演示：跨档口平衡缺餐'), actor)
        db.flush()
        transfers.dispatch(db, order.id, 50, admin.email)
        transfers.receive(db, order.id, 35, admin.email)
        transfers.return_order(db, order.id, 15, admin.email)
        db.add(Audit(actor='seed', action='demo.kitchen',
                     detail={'plan': plan.id, 'order': order.id}, at=time.time()))
        db.flush()


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        baseline=db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name']))
        if not baseline:
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
            baseline=j
        # 厨房演示数据（幂等）
        admin=db.scalar(select(User).where(User.role=='admin'))
        _seed_kitchen(db, admin, baseline)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,Stall,Dish,PrepPlan,PrepEntry,TransferOrder,TransferEvent,StockBalance]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()

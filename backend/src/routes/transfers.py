from fastapi import APIRouter, Depends
from src.config.database import get_db
from src.schemas.kitchen import TransferOrderInput, TransferActionInput
from src.middlewares.auth import current_user, can_write
from src.services import transfers
from src.services.audit import record
from src.services.idempotency import idempotent
from src.utils.serialization import order_dict, event_dict

router = APIRouter(prefix='/transfers')


@router.get('')
def listing(status: str | None = None, db=Depends(get_db), u=Depends(current_user)):
    return [order_dict(o) for o in transfers.list_orders(db, status)]


@router.post('', status_code=201)
def create(data: TransferOrderInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        o = transfers.create_order(db, data, u)
        record(db, u.email, 'transfer.create',
               {'id': o.id, 'order_no': o.order_no, 'qty': o.request_qty})
        return order_dict(o)
    body, _ = idempotent(db, 'transfer.create', data.idempotency_key, u.email, producer)
    db.commit()
    return body


@router.get('/{id}')
def detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    o, events = transfers.order_detail(db, id)
    return {'order': order_dict(o), 'events': [event_dict(e) for e in events]}


@router.post('/{id}/dispatch')
def dispatch(id: int, data: TransferActionInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        o = transfers.dispatch(db, id, data.qty, u.email)
        record(db, u.email, 'transfer.dispatch',
               {'id': id, 'qty': data.qty, 'status': o.status})
        return order_dict(o)
    body, _ = idempotent(db, 'transfer.dispatch', data.idempotency_key, u.email, producer)
    db.commit()
    return body


@router.post('/{id}/receive')
def receive(id: int, data: TransferActionInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        o = transfers.receive(db, id, data.qty, u.email)
        record(db, u.email, 'transfer.receive',
               {'id': id, 'qty': data.qty, 'status': o.status})
        return order_dict(o)
    body, _ = idempotent(db, 'transfer.receive', data.idempotency_key, u.email, producer)
    db.commit()
    return body


@router.post('/{id}/return')
def return_order(id: int, data: TransferActionInput, db=Depends(get_db), u=Depends(can_write)):
    def producer():
        o = transfers.return_order(db, id, data.qty, u.email)
        record(db, u.email, 'transfer.return',
               {'id': id, 'qty': data.qty, 'status': o.status})
        return order_dict(o)
    body, _ = idempotent(db, 'transfer.return', data.idempotency_key, u.email, producer)
    db.commit()
    return body


@router.post('/{id}/recover')
def recover(id: int, db=Depends(get_db), u=Depends(can_write)):
    """处理中断后的状态恢复（按恒等式重算结清状态）。"""
    o = transfers.settle_review(db, id)
    record(db, u.email, 'transfer.recover', {'id': id, 'status': o.status})
    db.commit()
    return order_dict(o)

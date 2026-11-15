import hashlib, json
from src.constants.enums import TRANSFER_STATUS_LABELS, TRANSFER_EVENT_LABELS
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    return {k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at']}
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    return {k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}

def stall_dict(s):
    return {'id':s.id,'code':s.code,'name':s.name,'active':s.active,'created_at':s.created_at}

def dish_dict(d):
    return {'id':d.id,'code':d.code,'name':d.name,'unit':d.unit,'active':d.active,'created_at':d.created_at}

def plan_dict(p):
    return {k:getattr(p,k) for k in ['id','data_date','dish_id','stall_id','suggested_qty',
            'approved_job_id','status','note','created_by','created_at','approved_at']}

def entry_dict(e):
    return {k:getattr(e,k) for k in ['id','plan_id','batch_no','prepared_qty','consumed_qty',
            'note','created_by','created_at']}

def consumption_dict(c):
    return {k:getattr(c,k) for k in ['id','entry_id','stall_id','dish_id','qty','purpose',
            'created_by','created_at']}

def order_dict(o):
    data = {k:getattr(o,k) for k in ['id','order_no','plan_id','dish_id','from_stall_id',
            'to_stall_id','request_qty','dispatched_qty','received_qty','returned_qty',
            'status','note','created_by','created_at','dispatched_at','settled_at','version']}
    data['status_label'] = TRANSFER_STATUS_LABELS.get(o.status, o.status)
    data['on_road_qty'] = o.dispatched_qty - o.received_qty - o.returned_qty
    return data

def event_dict(e):
    data = {k:getattr(e,k) for k in ['id','order_id','event_type','qty','from_status',
            'to_status','actor','detail','created_at']}
    data['event_label'] = TRANSFER_EVENT_LABELS.get(e.event_type, e.event_type)
    data['from_status_label'] = TRANSFER_STATUS_LABELS.get(e.from_status, e.from_status)
    data['to_status_label'] = TRANSFER_STATUS_LABELS.get(e.to_status, e.to_status)
    return data


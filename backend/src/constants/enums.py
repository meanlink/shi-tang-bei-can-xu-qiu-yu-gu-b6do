from enum import Enum
class Role(str, Enum):
    admin = 'admin'
    analyst = 'analyst'
    viewer = 'viewer'
class JobStatus(str, Enum):
    queued = 'queued'
    running = 'running'
    completed = 'completed'
    failed = 'failed'

class PrepStatus(str, Enum):
    pending = 'pending'      # 待批准
    approved = 'approved'    # 已批准，可登记实备

class TransferStatus(str, Enum):
    pending = 'pending'                      # 待发
    in_transit = 'in_transit'                # 在途
    completed_received = 'completed_received'        # 全部接收结清
    completed_returned = 'completed_returned'        # 全部退回结清
    partially_settled = 'partially_settled'          # 部分接收 + 余量退回，已结清

class TransferEvent(str, Enum):
    dispatch = 'dispatch'
    receive = 'receive'
    return_ = 'return'
    settle = 'settle'

# 状态中文标签（前端展示同步使用）
TRANSFER_STATUS_LABELS = {
    'pending': '待发',
    'in_transit': '在途',
    'completed_received': '已结清 · 全量接收',
    'completed_returned': '已结清 · 全量退回',
    'partially_settled': '已结清 · 部分接收',
}
TRANSFER_EVENT_LABELS = {
    'dispatch': '调出确认',
    'receive': '接收确认',
    'return': '退回确认',
    'settle': '结清',
}

export const Role={admin:'业务负责人',analyst:'分析员',viewer:'观察员'};
export const JobStatus={queued:'排队中',running:'计算中',completed:'已完成',failed:'执行失败'};
export const PrepStatus={pending:'待批准',approved:'已批准'};
export const TransferStatus={
 pending:'待发',
 in_transit:'在途',
 completed_received:'已结清 · 全量接收',
 completed_returned:'已结清 · 全量退回',
 partially_settled:'已结清 · 部分接收',
};
export const TransferEvent={
 dispatch:'调出确认',receive:'接收确认',return:'退回确认',settle:'结清',
};

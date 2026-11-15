import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};

const post=(path,data)=>api(path,{method:'POST',body:JSON.stringify(data)});
export const kitchen={
 stalls:()=>api('/kitchen/stalls'),
 createStall:data=>post('/kitchen/stalls',data),
 dishes:()=>api('/kitchen/dishes'),
 createDish:data=>post('/kitchen/dishes',data),
 plans:()=>api('/kitchen/plans'),
 createPlan:data=>post('/kitchen/plans',data),
 approvePlan:id=>post('/kitchen/plans/'+id+'/approve'),
 entries:planId=>api('/kitchen/entries'+(planId?'?plan_id='+planId:'')),
 registerEntry:data=>post('/kitchen/entries',data),
 consumptions:()=>api('/kitchen/consumptions'),
 consume:data=>post('/kitchen/consumptions',data),
 inventory:()=>api('/kitchen/inventory'),
 conservation:()=>api('/kitchen/conservation'),
};
export const transfers={
 list:status=>api('/transfers'+(status?'?status='+status:'')),
 get:id=>api('/transfers/'+id),
 create:data=>post('/transfers',data),
 dispatch:(id,data)=>post('/transfers/'+id+'/dispatch',data),
 receive:(id,data)=>post('/transfers/'+id+'/receive',data),
 returnOrder:(id,data)=>post('/transfers/'+id+'/return',data),
 recover:id=>post('/transfers/'+id+'/recover'),
};

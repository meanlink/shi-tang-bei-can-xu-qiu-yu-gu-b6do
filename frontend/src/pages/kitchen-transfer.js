import {kitchen,transfers} from '../api/entities.js';
import {session} from '../stores/session.js';
import {esc,fmt,date,notify,idemKey} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {TransferStatus,TransferEvent} from '../constants/enums.js';

const canWrite=()=>session.user.role!=='viewer';

/** 调拨标签：创建调拨单、状态操作（调出/接收/退回/恢复）与逐笔数量追查。 */
export async function transferTab(selectedId){
 const [stalls,dishes,plans,orders]=await Promise.all([
   kitchen.stalls(),kitchen.dishes(),kitchen.plans(),transfers.list()]);
 const stallName=id=>(stalls.find(s=>s.id===id)||{}).name||('#'+id);
 const dishName=id=>(dishes.find(d=>d.id===id)||{}).name||('#'+id);
 const approvedPlans=plans.filter(p=>p.status==='approved');

 const rows=orders.map(o=>({
   id:o.id, no:o.order_no, dish:dishName(o.dish_id),
   from:stallName(o.from_stall_id), to:stallName(o.to_stall_id),
   request:o.request_qty, dispatched:o.dispatched_qty, received:o.received_qty,
   returned:o.returned_qty, road:o.on_road_qty,
   status:o.status_label, plan:o.plan_id?('方案#'+o.plan_id):'—',
 }));

 const detail=selectedId?await transfers.get(selectedId).catch(()=>null):null;

 return `
 <div class="kitchen-stack">
  <section class="panel">
   <div class="section-title"><h2>跨档口调拨单</h2><span>${orders.length} 单</span></div>
   <div class="table-wrap"><table><thead><tr>
     <th>单号</th><th>餐品</th><th>调出档口</th><th>接收档口</th><th>申请</th>
     <th>已调出</th><th>已接收</th><th>已退回</th><th>在途余量</th><th>状态</th><th>关联方案</th><th>追查</th>
   </tr></thead><tbody>${rows.map(r=>'<tr><td>'+esc(r.no)+'</td><td>'+esc(r.dish)+'</td><td>'+esc(r.from)+'</td><td>'+esc(r.to)+'</td><td>'+esc(fmt(r.request))+'</td><td>'+esc(fmt(r.dispatched))+'</td><td>'+esc(fmt(r.received))+'</td><td>'+esc(fmt(r.returned))+'</td><td><strong>'+esc(fmt(r.road))+'</strong></td><td><span class="status '+statusClass(r.status)+'">'+esc(r.status)+'</span></td><td>'+esc(r.plan)+'</td><td><button class="button small" data-view-order="'+r.id+'">追查 ↗</button></td></tr>').join('')||'<tr><td colspan="12" class="empty" style="border:0">暂无调拨单</td></tr>'}</tbody></table></div>
   ${canWrite()?`<form id="transfer-form" class="kitchen-form">
     <div class="parameter-grid">
      <label>餐品<select name="dish_id" required>${dishes.map(d=>'<option value="'+d.id+'">'+esc(d.name)+'</option>').join('')}</select></label>
      <label>调出档口<select name="from_stall_id" required>${stalls.map(s=>'<option value="'+s.id+'">'+esc(s.name)+'</option>').join('')}</select></label>
      <label>接收档口<select name="to_stall_id" required>${stalls.map(s=>'<option value="'+s.id+'">'+esc(s.name)+'</option>').join('')}</select></label>
      <label>申请调拨量<input name="request_qty" type="number" min="1" step="1" required></label>
      <label>关联备餐方案（可选）<select name="plan_id"><option value="">不关联</option>${approvedPlans.map(p=>'<option value="'+p.id+'">#'+p.id+' · '+esc(p.data_date)+' · '+esc(dishName(p.dish_id))+' · '+esc(stallName(p.stall_id))+'</option>').join('')}</select></label>
      <label>备注<input name="note" maxlength="300" placeholder="可选"></label>
     </div>
     <button class="primary" type="submit">创建调拨单（待发）</button>
     <p class="hint">调出确认后餐量进入在途，接收确认才计入接收档口可用量；接收与退回同一时刻只认一方。</p>
    </form>`:''}
  </section>
  ${detail?renderDetail(detail,stallName,dishName):''}
 </div>`;
}

function statusClass(label){
 if(label.includes('在途'))return 'running';
 if(label.includes('待发'))return 'queued';
 if(label.includes('退回'))return 'failed';
 return 'completed';
}

function renderDetail(detail,stallName,dishName){
 const o=detail.order, events=detail.events;
 const settled=o.status!=='pending'&&o.status!=='in_transit';
 const eventRows=events.map(e=>({
   time:date(e.created_at), type:e.event_label,
   from:e.from_status_label||'—', to:e.to_status_label||'—',
   qty:e.qty, actor:e.actor, detail:JSON.stringify(e.detail||{}),
 }));
 return `
 <section class="panel method-sheet compact" id="order-detail">
  <div class="section-title"><h2>追查 · ${esc(o.order_no)}</h2><span>${esc(o.status_label)}</span></div>
  <div class="metrics">
   <div><span>餐品</span><strong style="font-size:16px">${esc(dishName(o.dish_id))}</strong></div>
   <div><span>调出 → 接收</span><strong style="font-size:16px">${esc(stallName(o.from_stall_id))} → ${esc(stallName(o.to_stall_id))}</strong></div>
   <div><span>申请 / 已调出</span><strong>${esc(fmt(o.request_qty))}<small>/${esc(fmt(o.dispatched_qty))}</small></strong></div>
   <div><span>已接收</span><strong>${esc(fmt(o.received_qty))}</strong></div>
   <div><span>已退回</span><strong>${esc(fmt(o.returned_qty))}</strong></div>
   <div><span>在途余量</span><strong>${esc(fmt(o.on_road_qty))}</strong></div>
   <div><span>关联备餐方案</span><strong style="font-size:16px">${o.plan_id?('方案#'+o.plan_id):'—'}</strong></div>
  </div>
  ${canWrite()?`
  <div class="transfer-actions">
   ${o.status==='pending'?actionBlock('dispatch',o.id,o.request_qty,'调出确认（进入在途）'):''}
   ${o.status==='in_transit'?actionBlock('receive',o.id,o.on_road_qty,'接收确认（计入可用量）')+actionBlock('return',o.id,o.on_road_qty,'退回确认（回到原档口）'):''}
   ${settled?'<p class="hint">该单已结清。若处理中断导致状态异常，可尝试恢复校正。</p><button class="button small" data-recover="'+o.id+'">状态恢复校正 ↻</button>':''}
  </div>`:''}
  <div class="section-title" style="margin-top:24px"><h2>数量变化事件流水</h2><span>${events.length} 个事件</span></div>
  <div class="table-wrap"><table><thead><tr><th>时间</th><th>事件</th><th>数量</th><th>原状态</th><th>新状态</th><th>操作者</th><th>明细</th></tr></thead>
   <tbody>${eventRows.map(r=>'<tr><td>'+esc(r.time)+'</td><td>'+esc(r.type)+'</td><td>'+esc(fmt(r.qty))+'</td><td>'+esc(r.from)+'</td><td>'+esc(r.to)+'</td><td>'+esc(r.actor)+'</td><td>'+esc(r.detail)+'</td></tr>').join('')}</tbody></table></div>
 </section>`;
}

function actionBlock(kind,id,maxQty,label){
 return `<form class="inline-action" data-action="${kind}" data-id="${id}">
   <label>${esc(label)}<input name="qty" type="number" min="1" max="${maxQty}" step="1" value="${maxQty}" required></label>
   <button class="primary small" type="submit">确认 ↗</button>
 </form>`;
}

export function bindTransfer(refresh){
 const form=document.querySelector('#transfer-form');
 form&&submit(form,async f=>{
   const planRaw=f.get('plan_id');
   await transfers.create({dish_id:Number(f.get('dish_id')),
     from_stall_id:Number(f.get('from_stall_id')),to_stall_id:Number(f.get('to_stall_id')),
     request_qty:Number(f.get('request_qty')),plan_id:planRaw?Number(planRaw):null,
     note:f.get('note')||'',idempotency_key:idemKey('tr')});
   notify('调拨单已创建（待发）');await refresh();
 });
 document.querySelectorAll('[data-view-order]').forEach(b=>b.onclick=()=>{
   location.hash='#/kitchen/transfer/'+b.dataset.viewOrder;
 });
 document.querySelectorAll('[data-recover]').forEach(b=>b.onclick=async()=>{
   try{await transfers.recover(Number(b.dataset.recover));notify('已按数量恒等式校正状态');await refresh();}
   catch(e){notify(e.message,true);}
 });
 document.querySelectorAll('.inline-action').forEach(formEl=>{
   formEl.addEventListener('submit',async e=>{
     e.preventDefault();
     const kind=formEl.dataset.action, id=Number(formEl.dataset.id);
     const qty=Number(new FormData(formEl).get('qty'));
     const key=idemKey(kind);
     try{
       if(kind==='dispatch')await transfers.dispatch(id,{qty,idempotency_key:key});
       if(kind==='receive')await transfers.receive(id,{qty,idempotency_key:key});
       if(kind==='return')await transfers.returnOrder(id,{qty,idempotency_key:key});
       notify('操作成功，库存与事件流水已更新');await refresh();
     }catch(err){notify(err.message,true);}
   });
 });
}

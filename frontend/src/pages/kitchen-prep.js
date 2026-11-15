import {kitchen,jobs} from '../api/entities.js';
import {session} from '../stores/session.js';
import {esc,fmt,notify,idemKey} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {PrepStatus} from '../constants/enums.js';

const canWrite=()=>session.user.role!=='viewer';
const isAdmin=()=>session.user.role==='admin';

/** 备餐台账标签：方案（关联已批准建议）→ 分次实备 → 领用耗用 → 库存与守恒。 */
export async function prepTab(){
 const [stalls,dishes,plans,entries,consumptions,inventory,conservation,allJobs]=await Promise.all([
   kitchen.stalls(),kitchen.dishes(),kitchen.plans(),kitchen.entries(),
   kitchen.consumptions(),kitchen.inventory(),kitchen.conservation(),jobs.list()]);
 const approvedJobs=allJobs.filter(j=>j.approved&&j.status==='completed');
 const stallName=id=>(stalls.find(s=>s.id===id)||{}).name||('#'+id);
 const dishName=id=>(dishes.find(d=>d.id===id)||{}).name||('#'+id);
 const planById=id=>plans.find(p=>p.id===id);

 const planRows=plans.map(p=>({
   id:p.id, date:p.data_date, dish:dishName(p.dish_id), stall:stallName(p.stall_id),
   suggested:p.suggested_qty, job:p.approved_job_id?('任务#'+p.approved_job_id):'—',
   status:PrepStatus[p.status]||p.status, approved_at:p.approved_at,
 }));
 const entryRows=entries.map(e=>{
   const p=planById(e.plan_id)||{};
   return {id:e.id, plan:'方案#'+e.plan_id, dish:dishName(p.dish_id), stall:stallName(p.stall_id),
     batch:'第 '+e.batch_no+' 批', prepared:e.prepared_qty, consumed:e.consumed_qty, note:e.note};
 });
 const consRows=consumptions.map(c=>({
   id:c.id, entry:'批次#'+c.entry_id, dish:dishName(c.dish_id), stall:stallName(c.stall_id),
   qty:c.qty, purpose:c.purpose||'—',
 }));
 const invRows=inventory.map(r=>({
   stall:r.stall_name, dish:r.dish_name, prepared:r.prepared_qty,
   available:r.available_qty, out_road:r.out_in_transit_qty, in_road:r.in_in_transit_qty,
   consumed:r.consumed_qty,
 }));
 const conservationRows=conservation.map(c=>({
   dish:c.dish_name, prepared:c.prepared, available:c.available, consumed:c.consumed,
   in_transit:c.in_transit, result:c.balanced?'守恒 ✓':'不平衡 ✗',
 }));

 return `
 <div class="kitchen-stack">
  <section class="panel">
   <div class="section-title"><h2>备餐方案 · 按已批准建议建立</h2><span>${plans.length} 个方案</span></div>
   <div class="table-wrap"><table><thead><tr>
     <th>业务日期</th><th>餐品</th><th>档口</th><th>建议量</th><th>关联备餐方案/分析</th><th>状态</th><th>操作</th>
   </tr></thead><tbody>${planRows.map(p=>'<tr><td>'+esc(p.date)+'</td><td>'+esc(p.dish)+'</td><td>'+esc(p.stall)+'</td><td>'+esc(fmt(p.suggested))+'</td><td>'+esc(p.job)+'</td><td><span class="status '+(p.status==='已批准'?'completed':'queued')+'">'+esc(p.status)+'</span></td><td>'+(p.status==='待批准'&&isAdmin()?'<button class="button small" data-approve-plan="'+p.id+'">批准方案</button>':'—')+'</td></tr>').join('')||emptyRow(7)}</tbody></table></div>
   ${canWrite()?`<form id="plan-form" class="kitchen-form">
     <div class="parameter-grid">
      <label>业务日期<input name="data_date" type="date" required value="${new Date().toISOString().slice(0,10)}"></label>
      <label>餐品<select name="dish_id" required>${dishes.map(d=>'<option value="'+d.id+'">'+esc(d.name)+'</option>').join('')}</select></label>
      <label>档口<select name="stall_id" required>${stalls.map(s=>'<option value="'+s.id+'">'+esc(s.name)+'</option>').join('')}</select></label>
      <label>建议备餐量<input name="suggested_qty" type="number" min="1" step="1" required placeholder="来自已批准建议"></label>
      <label>关联已批准分析（可选）<select name="approved_job_id"><option value="">不关联</option>${approvedJobs.map(j=>'<option value="'+j.id+'">'+esc(j.name)+' #'+j.id+'</option>').join('')}</select></label>
      <label>备注<input name="note" maxlength="300" placeholder="可选"></label>
     </div>
     <button class="primary" type="submit">建立备餐方案 ↗</button>
    </form>`:''}
  </section>

  <section class="panel">
   <div class="section-title"><h2>备餐台账 · 实备分次登记</h2><span>${entries.length} 个批次</span></div>
   <div class="table-wrap"><table><thead><tr>
     <th>批次</th><th>餐品</th><th>档口</th><th>实备量</th><th>已耗用</th><th>备注</th>
   </tr></thead><tbody>${entryRows.map(e=>'<tr><td>'+esc(e.plan)+' · '+esc(e.batch)+'</td><td>'+esc(e.dish)+'</td><td>'+esc(e.stall)+'</td><td>'+esc(fmt(e.prepared))+'</td><td>'+esc(fmt(e.consumed))+'</td><td>'+esc(e.note||'—')+'</td></tr>').join('')||emptyRow(6)}</tbody></table></div>
   ${canWrite()?`<form id="entry-form" class="kitchen-form">
     <div class="parameter-grid">
      <label>已批准方案<select name="plan_id" required>${plans.filter(p=>p.status==='approved').map(p=>'<option value="'+p.id+'">#'+p.id+' · '+esc(p.data_date)+' · '+esc(dishName(p.dish_id))+' · '+esc(stallName(p.stall_id))+'</option>').join('')||'<option value="">暂无已批准方案</option>'}</select></label>
      <label>本批实备量<input name="prepared_qty" type="number" min="1" step="1" required></label>
      <label>备注<input name="note" maxlength="300" placeholder="可选"></label>
     </div>
     <button class="primary" type="submit" ${plans.some(p=>p.status==='approved')?'':'disabled'}>登记实备（自动幂等键）</button>
    </form>`:''}
  </section>

  <section class="panel">
   <div class="section-title"><h2>领用耗用</h2><span>${consumptions.length} 笔</span></div>
   <div class="table-wrap"><table><thead><tr><th>批次</th><th>餐品</th><th>档口</th><th>领用份数</th><th>用途</th></tr></thead>
   <tbody>${consRows.map(c=>'<tr><td>'+esc(c.entry)+'</td><td>'+esc(c.dish)+'</td><td>'+esc(c.stall)+'</td><td>'+esc(fmt(c.qty))+'</td><td>'+esc(c.purpose)+'</td></tr>').join('')||emptyRow(5)}</tbody></table></div>
   ${canWrite()?`<form id="consume-form" class="kitchen-form">
     <div class="parameter-grid">
      <label>实备批次<select name="entry_id" required>${entries.map(e=>{const p=planById(e.plan_id)||{};return '<option value="'+e.id+'">#'+e.id+' · '+esc(dishName(p.dish_id))+' · 剩 '+(e.prepared_qty-e.consumed_qty)+'</option>';}).join('')||'<option value="">暂无批次</option>'}</select></label>
      <label>领用份数<input name="qty" type="number" min="1" step="1" required></label>
      <label>用途<input name="purpose" maxlength="160" placeholder="例如：午餐核销"></label>
     </div>
     <button class="primary" type="submit" ${entries.length?'':'disabled'}>登记领用耗用</button>
    </form>`:''}
  </section>

  <section class="panel">
   <div class="section-title"><h2>各档口可用量 / 在途量 / 累计耗用</h2><span>实备总量始终守恒</span></div>
   ${renderTable(['档口','餐品','实备总量','当前可用','调出在途','调入在途','累计耗用'],invRows,['stall','dish','prepared','available','out_road','in_road','consumed'])}
  </section>

  <section class="panel">
   <div class="section-title"><h2>全局守恒复核</h2><span>实备 = 可用 + 耗用 + 在途</span></div>
   ${renderTable(['餐品','实备总量','可用合计','累计耗用','全局在途','校验结果'],conservationRows,['dish','prepared','available','consumed','in_transit','result'])}
  </section>
 </div>`;
}

function emptyRow(span){return '<tr><td colspan="'+span+'" class="empty" style="border:0">暂无记录</td></tr>';}
function renderTable(headers,rows,keys){
 if(!rows.length)return '<div class="empty">暂无库存记录，先登记实备。</div>';
 return '<div class="table-wrap"><table><thead><tr>'+headers.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'
   +rows.map(r=>'<tr>'+keys.map(k=>'<td>'+esc(fmt(r[k]))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
}

export function bindPrep(refresh){
 const planForm=document.querySelector('#plan-form');
 planForm&&submit(planForm,async f=>{
   const jobRaw=f.get('approved_job_id');
   await kitchen.createPlan({data_date:f.get('data_date'),dish_id:Number(f.get('dish_id')),
     stall_id:Number(f.get('stall_id')),suggested_qty:Number(f.get('suggested_qty')),
     approved_job_id:jobRaw?Number(jobRaw):null,note:f.get('note')||''});
   notify('备餐方案已建立，待负责人批准');await refresh();
 });
 document.querySelectorAll('[data-approve-plan]').forEach(b=>b.onclick=async()=>{
   try{await kitchen.approvePlan(Number(b.dataset.approvePlan));notify('方案已批准，可登记实备');await refresh();}
   catch(e){notify(e.message,true);}
 });
 const entryForm=document.querySelector('#entry-form');
 entryForm&&submit(entryForm,async f=>{
   await kitchen.registerEntry({plan_id:Number(f.get('plan_id')),prepared_qty:Number(f.get('prepared_qty')),
     note:f.get('note')||'',idempotency_key:idemKey('prep')});
   notify('实备已登记，可用量已更新');await refresh();
 });
 const consumeForm=document.querySelector('#consume-form');
 consumeForm&&submit(consumeForm,async f=>{
   await kitchen.consume({entry_id:Number(f.get('entry_id')),qty:Number(f.get('qty')),
     purpose:f.get('purpose')||'',idempotency_key:idemKey('cons')});
   notify('领用耗用已记录');await refresh();
 });
}

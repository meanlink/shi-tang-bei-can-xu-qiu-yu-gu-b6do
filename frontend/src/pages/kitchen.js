import {kitchen} from '../api/entities.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';
import {prepTab,bindPrep} from './kitchen-prep.js';
import {transferTab,bindTransfer} from './kitchen-transfer.js';

const isAdmin=()=>session.user.role==='admin';

/** 厨房备餐与跨档口调拨：备餐台账 / 调拨追查两个标签。 */
export async function kitchenPage(tab='prep',subId){
 const [stalls,dishes]=await Promise.all([kitchen.stalls(),kitchen.dishes()]);
 const nav=[['prep','备餐台账与库存'],['transfer','跨档口调拨追查']];
 const body=tab==='transfer'?await transferTab(subId):await prepTab();
 return heading('06 / KITCHEN OPS','厨房备餐与跨档口调拨',
   '每日按已批准建议建立备餐台账，实备分次登记并记录领用耗用；调出进入在途，接收才计入可用，退回回到原档口，全局数量始终守恒。')
 +`<div class="kitchen-tabs">${nav.map(n=>'<a class="'+(tab===n[0]?'active':'')+'" href="#/kitchen/'+n[0]+'">'+esc(n[1])+'</a>').join('')}</div>`
 + renderMaster(stalls,dishes)
 + body;
}

function renderMaster(stalls,dishes){
 if(!isAdmin())return '';
 return `<section class="panel">
  <div class="section-title"><h2>基础资料 · 档口与餐品</h2><span>仅业务负责人可维护</span></div>
  <div class="master-grid">
   <div><h3>档口（${stalls.length}）</h3>
    <div class="master-list">${stalls.map(s=>'<span>'+esc(s.code)+' · '+esc(s.name)+'</span>').join('')}</div>
    <form id="stall-form" class="kitchen-form"><div class="parameter-grid">
      <label>编码<input name="code" required maxlength="32" placeholder="如 D"></label>
      <label>名称<input name="name" required maxlength="80" placeholder="如 蒸煮档口"></label>
    </div><button class="primary small" type="submit">新增档口</button></form>
   </div>
   <div><h3>餐品（${dishes.length}）</h3>
    <div class="master-list">${dishes.map(d=>'<span>'+esc(d.code)+' · '+esc(d.name)+'（'+esc(d.unit)+'）</span>').join('')}</div>
    <form id="dish-form" class="kitchen-form"><div class="parameter-grid">
      <label>编码<input name="code" required maxlength="32" placeholder="如 D3"></label>
      <label>名称<input name="name" required maxlength="80" placeholder="如 清蒸鱼"></label>
      <label>单位<input name="unit" maxlength="16" value="份"></label>
    </div><button class="primary small" type="submit">新增餐品</button></form>
   </div>
  </div>
 </section>`;
}

export function bindKitchen(tab,subId,refresh){
 const stallForm=document.querySelector('#stall-form');
 stallForm&&submit(stallForm,async f=>{
   await kitchen.createStall({code:f.get('code'),name:f.get('name')});
   notify('档口已新增');await refresh();
 });
 const dishForm=document.querySelector('#dish-form');
 dishForm&&submit(dishForm,async f=>{
   await kitchen.createDish({code:f.get('code'),name:f.get('name'),unit:f.get('unit')||'份'});
   notify('餐品已新增');await refresh();
 });
 if(tab==='transfer')bindTransfer(refresh);else bindPrep(refresh);
}

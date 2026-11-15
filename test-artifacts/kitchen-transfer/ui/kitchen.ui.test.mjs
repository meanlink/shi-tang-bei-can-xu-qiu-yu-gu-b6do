// jsdom 渲染与交互验收：验证厨房页面模板、数据绑定、状态操作与幂等键。
// 运行：在本目录执行  npm install && node kitchen.ui.test.mjs
import {JSDOM} from 'jsdom';
import path from 'path';
import {fileURLToPath, pathToFileURL} from 'url';

const __dirname=path.dirname(fileURLToPath(import.meta.url));
const FRONTEND=path.resolve(__dirname,'..','..','..','frontend','src');
const dom=new JSDOM('<!doctype html><html><body><div id="app"></div><div id="toast"></div></body></html>',{url:'http://localhost/'});
globalThis.window=dom.window;globalThis.document=dom.window.document;
globalThis.localStorage=dom.window.localStorage;
if(!globalThis.crypto?.randomUUID)globalThis.crypto=dom.window.crypto;
globalThis.Event=dom.window.Event;globalThis.CustomEvent=dom.window.CustomEvent;
globalThis.FormData=dom.window.FormData;globalThis.HTMLElement=dom.window.HTMLElement;
globalThis.location=dom.window.location;globalThis.history=dom.window.history;
globalThis.alert=()=>{};

// ---- fetch 桩：按路径返回模拟后端数据，并记录写请求 ----
const calls=[];
const stalls=[{id:1,code:'A',name:'热炒档口',active:true},{id:2,code:'B',name:'面点档口',active:true}];
const dishes=[{id:1,code:'D1',name:'红烧肉',unit:'份',active:true}];
const plans=[{id:1,data_date:'2026-11-15',dish_id:1,stall_id:1,suggested_qty:120,approved_job_id:7,status:'approved',note:'',created_by:1,created_at:1,approved_at:2}];
const entries=[{id:1,plan_id:1,batch_no:1,prepared_qty:80,consumed_qty:0,note:'首批',created_by:1,created_at:1}];
const consumptions=[];
const inventory=[
 {stall_id:1,stall_name:'热炒档口',dish_id:1,dish_name:'红烧肉',prepared_qty:120,consumed_qty:0,out_in_transit_qty:0,in_in_transit_qty:0,available_qty:85,dispatched_qty:50,received_qty:0,returned_qty:15,ledger_prepared_qty:120,ledger_consumed_qty:0},
 {stall_id:2,stall_name:'面点档口',dish_id:1,dish_name:'红烧肉',prepared_qty:0,consumed_qty:0,out_in_transit_qty:0,in_in_transit_qty:0,available_qty:35,dispatched_qty:0,received_qty:35,returned_qty:0,ledger_prepared_qty:0,ledger_consumed_qty:0},
];
const conservation=[{dish_id:1,dish_name:'红烧肉',prepared:120,available:120,consumed:0,in_transit:0,ledger_matched:true,balanced:true}];
const jobs=[{id:7,name:'基线复核',status:'completed',approved:true}];
const orders=[{id:11,order_no:'TR20261115-0001',plan_id:1,dish_id:1,from_stall_id:1,to_stall_id:2,request_qty:50,dispatched_qty:50,received_qty:35,returned_qty:15,status:'partially_settled',status_label:'已结清 · 部分接收',note:'',created_by:1,created_at:1,dispatched_at:2,settled_at:3,version:3,on_road_qty:0}];
const eventsDetail={order:orders[0],events:[
 {id:1,order_id:11,event_type:'dispatch',qty:50,from_status:'pending',to_status:'in_transit',actor:'a@t',detail:{},created_at:1,event_label:'调出确认',from_status_label:'待发',to_status_label:'在途'},
 {id:2,order_id:11,event_type:'receive',qty:35,from_status:'in_transit',to_status:'in_transit',actor:'a@t',detail:{},created_at:2,event_label:'接收确认',from_status_label:'在途',to_status_label:'在途'},
 {id:3,order_id:11,event_type:'return',qty:15,from_status:'in_transit',to_status:'partially_settled',actor:'a@t',detail:{},created_at:3,event_label:'退回确认',from_status_label:'在途',to_status_label:'已结清 · 部分接收'},
]};

globalThis.fetch=async (u,opt={})=>{
 const url=String(u).replace('/api','');calls.push({url,method:opt.method||'GET',body:opt.body?JSON.parse(opt.body):null});
 let data=null;
 if(url.startsWith('/kitchen/stalls'))data=stalls;
 else if(url.startsWith('/kitchen/dishes'))data=dishes;
 else if(url.startsWith('/kitchen/plans'))data=plans;
 else if(url.startsWith('/kitchen/entries'))data=entries;
 else if(url.startsWith('/kitchen/consumptions'))data=consumptions;
 else if(url.startsWith('/kitchen/inventory'))data=inventory;
 else if(url.startsWith('/kitchen/conservation'))data=conservation;
 else if(url.startsWith('/jobs'))data=jobs;
 else if(url.startsWith('/transfers/11'))data=eventsDetail;
 else if(url.startsWith('/transfers'))data=orders;
 else data={};
 return {ok:true,status:200,json:async()=>data};
};

localStorage.setItem('token','fake');localStorage.setItem('user',JSON.stringify({name:'负责人',role:'admin',email:'a@t'}));
location.hash='#/kitchen/prep';

const imp=p=>import(pathToFileURL(path.join(FRONTEND,p)).href);
let pass=0,fail=0;
const ok=(cond,msg)=>{cond?(pass++,console.log('  ✓',msg)):(fail++,console.error('  ✗',msg));};

// ---- 备餐标签渲染 ----
{
 const {kitchenPage}=await imp('pages/kitchen.js');
 const html=await kitchenPage('prep');
 document.querySelector('#app').innerHTML=html;
 ok(html.includes('厨房备餐与跨档口调拨'),'页面标题渲染');
 ok(html.includes('备餐台账与库存'),'备餐标签存在');
 ok(html.includes('跨档口调拨追查'),'调拨标签存在');
 ok(html.includes('红烧肉'),'餐品数据渲染');
 ok(html.includes('热炒档口'),'档口数据渲染');
 ok(html.includes('关联已批准分析'),'方案表单含已批准分析下拉');
 ok(html.includes('全局守恒复核'),'守恒复核区块渲染');
 ok(html.includes('守恒 ✓'),'守恒结果正确显示');
 ok(document.querySelector('#plan-form')!==null,'建方案表单存在');
 ok(document.querySelector('#entry-form')!==null,'实备登记表单存在');
 ok(document.querySelector('#consume-form')!==null,'领用表单存在');
 ok(document.querySelector('[data-approve-plan="1"]')===null,'已批准方案不再显示批准按钮');
 ok(document.querySelector('#stall-form')!==null,'admin 可见档口维护表单');
}

// ---- 备餐交互：提交实备登记携带幂等键 ----
{
 const {bindKitchen}=await imp('pages/kitchen.js');
 const refresh=async()=>{document.querySelector('#app').innerHTML=await (await imp('pages/kitchen.js')).kitchenPage('prep');bindKitchen('prep',null,refresh);};
 bindKitchen('prep',null,refresh);
 const form=document.querySelector('#entry-form');
 form.querySelector('[name=plan_id]').value='1';
 form.querySelector('[name=prepared_qty]').value='40';
 form.dispatchEvent(new dom.window.Event('submit',{cancelable:true,bubbles:true}));
 await new Promise(r=>setTimeout(r,30));
 const reg=calls.find(c=>c.url==='/kitchen/entries'&&c.method==='POST');
 ok(!!reg,'实备登记 POST 被调用');
 ok(reg.body.idempotency_key && reg.body.idempotency_key.startsWith('prep-'),'实备登记携带幂等键: '+reg.body?.idempotency_key);
 ok(reg.body.prepared_qty===40,'实备量正确提交');
}

// ---- 调拨标签渲染与追查 ----
{
 const {kitchenPage}=await imp('pages/kitchen.js');
 const html=await kitchenPage('transfer','11');
 document.querySelector('#app').innerHTML=html;
 ok(html.includes('TR20261115-0001'),'调拨单号渲染');
 ok(html.includes('已结清 · 部分接收'),'调拨状态中文渲染');
 ok(html.includes('数量变化事件流水'),'事件流水区块渲染');
 ok(html.includes('调出确认')&&html.includes('接收确认')&&html.includes('退回确认'),'三类事件标签齐全');
 ok((html.match(/红烧肉/g)||[]).length>=2,'餐品在单与明细中一致');
 ok(document.querySelector('[data-recover="11"]')!==null,'结清单提供状态恢复按钮');
 ok(document.querySelector('.inline-action')===null,'结清单不再显示调出/接收/退回操作');
 ok(html.includes('方案#1'),'调拨单关联备餐方案可追查');
}

// ---- viewer 角色：写操作入口隐藏 ----
{
 const {session}=await imp('stores/session.js');
 session.user={name:'观察员',role:'viewer',email:'v@t'};
 const {kitchenPage}=await imp('pages/kitchen.js');
 const html=await kitchenPage('transfer','11');
 ok(!html.includes('id="transfer-form"'),'viewer 看不到创建调拨表单');
 ok(!html.includes('id="stall-form"'),'viewer 看不到基础资料维护');
 session.user={name:'负责人',role:'admin',email:'a@t'};
}

console.log(`\n前端验收：${pass} 通过 / ${fail} 失败`);
process.exit(fail?1:0);

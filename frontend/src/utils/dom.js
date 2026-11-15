export const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const fmt=v=>typeof v==='number'?new Intl.NumberFormat('zh-CN',{maximumFractionDigits:4}).format(v):String(v??'—');
export const date=v=>new Date(v*1000).toLocaleString('zh-CN',{hour12:false});
export function notify(message,error=false){const t=document.querySelector('#toast');t.textContent=message;t.className=error?'show error':'show';setTimeout(()=>t.className='',5000);}
/** 生成业务幂等键：响应丢失重试时携带同一键，服务端只记一次账。 */
export function idemKey(prefix='op'){
 const rnd=(crypto?.randomUUID?.()||('id-'+Date.now()+'-'+Math.random().toString(36).slice(2)));
 return prefix+'-'+rnd;
}

let renewing;
export async function api(path, method='GET', body, retry=true) {
 const r=await fetch('/api/v1'+path,{method,headers:{Authorization:'Bearer '+(localStorage.getItem('mm.access')||''),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
 if(r.status===401){
  if(retry && localStorage.getItem('mm.refresh')){
   renewing ??= (async()=>{try{const r=await fetch('/api/v1/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:localStorage.getItem('mm.refresh')})});if(!r.ok)return false;const t=await r.json();localStorage.setItem('mm.access',t.access_token);localStorage.setItem('mm.refresh',t.refresh_token);return true;}finally{setTimeout(()=>{renewing=null;},0);}})();
   if(await renewing)return api(path,method,body,false);
  }
  localStorage.removeItem('mm.access');localStorage.removeItem('mm.refresh');location.href='/teacher/';throw Error('请重新登录');
 }
 if(!r.ok){const d=(await r.json()).detail;throw Error(typeof d==='string'?d:d?.message||'操作失败，请检查输入');}
 return r.status===204?null:r.json();
}

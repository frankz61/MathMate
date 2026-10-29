const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roles={teacher:'教师',group_leader:'教研组长',school_admin:'学校管理员'};
let items=[],classes=[],editing=null,resetting=null;
async function api(path,method='GET',body){
 const r=await fetch('/api/v1'+path,{method,headers:{Authorization:'Bearer '+localStorage.getItem('mm.access'),'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
 if(r.status===401){location.href='/teacher/';throw Error('请重新登录');}
 if(!r.ok){let d=await r.json();throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}
 return r.status===204?null:r.json();
}
function message(s){$('message').textContent=s;}
async function load(){items=await api('/teachers');$('rows').innerHTML=items.map(t=>`<tr><td>${esc(t.name)}<br><small>${esc(t.username)}</small></td><td>${roles[t.role]}</td><td>${t.status==='active'?'启用':'停用'}</td><td>${t.class_ids.map(id=>esc(classes.find(c=>c.id===id)?.name||id)).join('、')||'未分配'}</td><td><button data-edit="${esc(t.id)}">编辑</button><button data-reset="${esc(t.id)}">重置密码</button></td></tr>`).join('');}
function edit(t){editing=t?.id||null;const f=$('teacher');f.reset();$('title').textContent=t?'编辑教师':'新增教师';for(const k of ['name','username','role','status'])if(t)f.elements.namedItem(k).value=t[k];$('initial').hidden=!!t;f.elements.namedItem('password').required=!t;$('classes').innerHTML=classes.map(c=>`<label><input type="checkbox" value="${esc(c.id)}" ${t?.class_ids.includes(c.id)?'checked':''}>${esc(c.name)}</label>`).join('');$('editor').hidden=false;$('editor').scrollIntoView({behavior:'smooth'});}
$('new').onclick=()=>edit(null);$('cancel').onclick=()=>$('editor').hidden=true;
$('rows').onclick=e=>{const b=e.target.closest('button');if(!b)return;const t=items.find(t=>t.id===(b.dataset.edit||b.dataset.reset));if(b.dataset.edit)edit(t);else{resetting=t.id;$('reset').reset();$('reset-error').textContent='';$('reset-name').textContent=t.name+' / '+t.username;$('reset-dialog').showModal();}};
$('reset-cancel').onclick=()=>$('reset-dialog').close();
$('reset').onsubmit=async e=>{e.preventDefault();try{await api('/teachers/'+resetting+'/password','POST',{password:new FormData(e.target).get('password')});$('reset-dialog').close();message('密码已重置，请通知该教师重新登录。');}catch(err){$('reset-error').textContent=err.message;}};
$('teacher').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));data.class_ids=[...$('classes').querySelectorAll('input:checked')].map(i=>i.value);if(editing)delete data.password;try{await api(editing?'/teachers/'+editing:'/teachers',editing?'PUT':'POST',data);$('editor').hidden=true;message('已保存。');await load();}catch(err){message(err.message);}};
$('password').onsubmit=async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(e.target));if(data.password!==data.confirm)return message('两次新密码不一致');delete data.confirm;try{await api('/auth/password','POST',data);localStorage.removeItem('mm.access');localStorage.removeItem('mm.refresh');alert('密码已修改，请重新登录');location.href='/teacher/';}catch(err){message(err.message);}};
try{const me=await api('/auth/me');if(me.role==='school_admin'){classes=await api('/management/classes');$('admin').hidden=false;await load();}}catch(err){message(err.message);}

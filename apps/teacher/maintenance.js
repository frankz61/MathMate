import {api} from './management-api.js';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let me, cursor=null, generation=0;
const time=s=>s?new Date(/Z$|\+\d\d:\d\d$/.test(s)?s:s+'Z').toLocaleString():'尚未连接';
async function loadClass(){const id=$('class').value, run=++generation;if(!id)return;const [students,devices]=await Promise.all([api('/classes/'+id+'/students?status=inactive'),api('/screens?class_id='+encodeURIComponent(id))]);if(run!==generation)return;
 $('students').innerHTML=students.students.map(s=>`<tr><td>${esc(s.name)}</td><td>${esc(s.student_no||'—')}</td><td>${esc(s.seat_no)}</td><td>${me.role==='group_leader'?'只读':`<button data-restore="${esc(s.student_id)}">恢复</button>`}</td></tr>`).join('')||'<tr><td colspan="4">没有已停用学生</td></tr>';
 $('devices').innerHTML=devices.map(d=>`<tr><td>${esc(d.name)}</td><td>${d.status==='active'?'已配对':'等待配对'}</td><td>${time(d.last_seen_at)}</td><td>${me.role==='group_leader'?'只读':`<button data-revoke="${esc(d.id)}">解绑</button>`}</td></tr>`).join('')||'<tr><td colspan="4">没有已配对或等待配对的设备</td></tr>';
}
const labels={teachers:'教师',teacher_classes:'任教关系',classes:'班级',students:'学生',screen_devices:'大屏'};
const fields={name:'姓名/名称',username:'账号',role:'角色',status:'状态',class_id:'班级ID',teacher_id:'教师ID',student_no:'学号',seat_no:'座位',seat_layout:'座位布局',grade:'年级',token_version:'凭据版本'};
async function loadLogs(append=false){const result=await api('/management/audit'+(append&&cursor?'?before='+cursor:''));const html=result.items.map(r=>`<tr><td>${time(r.created_at)}</td><td>${esc(r.actor_name)}</td><td>${{create:'新增',update:'修改',delete:'移除'}[r.action]} / ${labels[r.object_type]||esc(r.object_type)}<br><small>${esc(r.object_id)}</small></td><td>${Object.entries(r.changes).map(([k,v])=>k==='password_changed'?'密码已设置或重置':`${fields[k]||esc(k)}：${esc(JSON.stringify(v.before))} → ${esc(JSON.stringify(v.after))}`).join('<br>')}</td></tr>`).join('');if(append)$('logs').insertAdjacentHTML('beforeend',html);else $('logs').innerHTML=html||'<tr><td colspan="4">暂无记录</td></tr>';cursor=result.next;$('more').hidden=!cursor;}
async function report(fn){try{await fn();}catch(e){$('message').textContent=e.message;}}
$('class').onchange=()=>report(loadClass);
$('students').onclick=e=>report(async()=>{const b=e.target.closest('[data-restore]');if(!b)return;b.disabled=true;try{await api('/students/'+b.dataset.restore+'/restore','POST');await loadClass();$('message').textContent='学生已恢复';}finally{b.disabled=false;}});
$('devices').onclick=e=>report(async()=>{const b=e.target.closest('[data-revoke]');if(!b||!confirm('确认解绑此大屏？设备需要重新配对才能使用。'))return;b.disabled=true;try{await api('/screens/'+b.dataset.revoke,'DELETE');await loadClass();$('message').textContent='设备已解绑';}finally{b.disabled=false;}});
$('reload').onclick=()=>report(()=>loadLogs());$('more').onclick=()=>report(()=>loadLogs(true));
await report(async()=>{me=await api('/auth/me');$('class').innerHTML=me.classes.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');if(!me.classes.length)$('message').textContent='暂无可访问班级';await loadClass();if(me.role==='school_admin'){$('audit').hidden=false;await loadLogs();}});

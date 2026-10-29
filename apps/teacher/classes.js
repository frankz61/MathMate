const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let items = [], editing = null, editable = false;
async function api(path, method = 'GET', body, retry = true) {
 const r = await fetch('/api/v1' + path, {method, headers: {'Content-Type':'application/json', Authorization:'Bearer ' + (localStorage.getItem('mm.access') || '')}, ...(body ? {body:JSON.stringify(body)} : {})});
 if (r.status === 401) {
  if (retry && localStorage.getItem('mm.refresh')) {
   const renewed = await fetch('/api/v1/auth/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({refresh_token:localStorage.getItem('mm.refresh')})});
   if (renewed.ok) {const token = await renewed.json(); localStorage.setItem('mm.access', token.access_token); localStorage.setItem('mm.refresh', token.refresh_token); return api(path, method, body, false);}
  }
  location.href = '/teacher/'; throw Error('请重新登录');
 }
 if (!r.ok) {const d = (await r.json()).detail; throw Error(typeof d === 'string' ? d : d?.message || '保存失败，请检查输入后重试');}
 return r.json();
}
async function load() {
 items = await api('/classes');
 $('rows').innerHTML = items.length ? items.map(c => `<tr><td>${esc(c.name)}</td><td>${{7:'七年级',8:'八年级',9:'九年级'}[c.grade] || esc(c.grade)}</td><td>${c.size}</td><td>${c.seat_layout.rows} 排 × ${c.seat_layout.cols} 座</td><td>${editable ? `<button data-edit="${esc(c.id)}">编辑</button>` : '只读'}</td></tr>`).join('') : '<tr><td colspan="5">暂无可访问的班级。</td></tr>';
}
function edit(c) {
 editing = c?.id || null;
 const form = $('class-form'); form.reset();
 form.elements.namedItem('name').value = c?.name || '';
 form.elements.namedItem('grade').value = c?.grade || 7;
 form.elements.namedItem('rows').value = c?.seat_layout.rows || 6;
 form.elements.namedItem('cols').value = c?.seat_layout.cols || 7;
 $('title').textContent = c ? '编辑班级' : '新增班级';
 $('form-error').textContent = ''; $('message').textContent = '';
 $('editor').hidden = false; $('editor').scrollIntoView({behavior:'smooth'}); form.elements.namedItem('name').focus();
}
$('new').onclick = () => edit(null);
$('cancel').onclick = () => {$('editor').hidden = true;};
$('rows').onclick = e => {const b = e.target.closest('button[data-edit]'); if (b) edit(items.find(c => c.id === b.dataset.edit));};
$('class-form').onsubmit = async e => {
 e.preventDefault();
 const data = Object.fromEntries(new FormData(e.target));
 data.name = data.name.trim();
 if (!data.name) {$('form-error').textContent = '班级名称不能为空'; return;}
 for (const k of ['grade','rows','cols']) data[k] = Number(data[k]);
 $('save').disabled = true; $('form-error').textContent = '';
 try {await api(editing ? '/classes/' + editing : '/classes', editing ? 'PATCH' : 'POST', data); $('editor').hidden = true; $('message').textContent = '班级已保存，返回教师控制台刷新后即可选择。'; await load();}
 catch (err) {$('form-error').textContent = err.message;}
 finally {$('save').disabled = false;}
};
try {
 const me = await api('/auth/me'); editable = me.role !== 'group_leader';
 $('new').hidden = !editable;
 $('scope').textContent = me.role === 'school_admin' ? '维护本校全部班级。可在账号管理中分配任教教师。' : me.role === 'group_leader' ? '可查看本校全部班级，当前角色为只读。' : '维护您任教的班级；新建班级会自动关联到您的账号。';
 await load();
} catch (err) {$('message').textContent = err.message;}

import os
import tempfile
os.environ.setdefault('MATHMATE_DB_URL','sqlite:///'+os.path.join(tempfile.mkdtemp(prefix='mathmate-hardening-'),'test.db'))
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import Teacher, Class, Student, Pet
from app.auth import hash_password
from app.seed import seed


def test_management_lifecycle_and_audit():
 seed(reset=True)
 with SessionLocal() as db:
  db.add_all([Teacher(username='hard_admin',name='管理员',role='school_admin',password_hash=hash_password('admin-password')),Teacher(username='hard_leader',name='组长',role='group_leader',password_hash=hash_password('leader-password'))]);db.commit()
 with TestClient(app) as c:
  def login(u,p):
   r=c.post('/api/v1/auth/login',json=dict(username=u,password=p));assert r.status_code==200,r.text
   return r.json()['access_token']
  token=login('teacher','mathmate'); th={'Authorization':'Bearer '+token}
  ah={'Authorization':'Bearer '+login('hard_admin','admin-password')}
  lh={'Authorization':'Bearer '+login('hard_leader','leader-password')}
  cls=c.get('/api/v1/classes',headers=th).json()[0];cid=cls['id'];aid=cls['assignments'][0]['id']
  sid=c.post('/api/v1/vote-sessions',headers=th,json=dict(class_id=cid,assignment_id=aid)).json()['id']
  for path,method,body in [('/students/missing','DELETE',None),('/classes/'+cid+'/students','POST',dict(name='越权',seat_no='1010')),('/assignments','POST',dict(class_id=cid,title='越权',assign_date='2026-10-28')),('/vote-sessions/'+sid+'/close','POST',None)]:
   assert c.request(method,'/api/v1'+path,headers=lh,json=body).status_code==403
  assert c.get('/api/v1/management/audit',headers=th).status_code==403
  me=c.get('/api/v1/auth/me',headers=th).json();tid=me['id']
  r=c.put('/api/v1/teachers/'+tid,headers=ah,json=dict(name='改名',username='teacher',role='teacher',status='active',class_ids=[cid]));assert r.status_code==200,r.text
  assert c.get('/api/v1/auth/me',headers=th).status_code==200
  student=c.get('/api/v1/classes/'+cid+'/students',headers=th).json()['students'][0];student_id=student['student_id']
  assert c.delete('/api/v1/students/'+student_id,headers=th).status_code==204
  with SessionLocal() as db:
   assert db.get(Student,student_id).status=='inactive'
   assert db.scalar(select(Pet).where(Pet.student_id==student_id)) is not None
  assert any(s['student_id']==student_id for s in c.get('/api/v1/classes/'+cid+'/students?status=inactive',headers=th).json()['students'])
  assert c.post('/api/v1/students/'+student_id+'/restore',headers=th).status_code==204
  with c.websocket_connect('/ws/vote/'+sid+'?access='+token) as ws:
   r=c.post('/api/v1/teachers/'+tid+'/password',headers=ah,json=dict(password='new-secret-password'));assert r.status_code==204,r.text
   with pytest.raises(WebSocketDisconnect): ws.receive_json()
  logs=c.get('/api/v1/management/audit',headers=ah).json()
  assert any(x['object_type']=='students' for x in logs['items'])
  assert any('password_changed' in x['changes'] for x in logs['items'])
  assert 'new-secret-password' not in str(logs) and 'password_hash' not in str(logs)
  assert c.get('/api/v1/management/audit?limit=1',headers=ah).json()['next'] is not None

import os
import tempfile

os.environ.setdefault("MATHMATE_DB_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="mathmate-classes-"), "test.db"))

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import Teacher, Class
from app.auth import hash_password
from app.seed import seed


def test_class_management_scope_and_layout():
    seed(reset=True)
    with SessionLocal() as db:
        db.add(Teacher(username='class_leader',name='组长',role='group_leader',password_hash=hash_password('leader-password')))
        db.add(Teacher(username='class_other',name='其他教师',password_hash=hash_password('other-password')))
        db.add(Teacher(username='class_admin',name='管理员',role='school_admin',password_hash=hash_password('admin-password')))
        foreign=Class(name='其他学校班级',grade=7,tenant_id='other-school')
        db.add(foreign); db.commit(); foreign_id=foreign.id
    with TestClient(app) as c:
        def login(u,p):
            r=c.post('/api/v1/auth/login',json=dict(username=u,password=p))
            assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['access_token']}
        th=login('teacher','mathmate')
        lh=login('class_leader','leader-password')
        oh=login('class_other','other-password')
        ah=login('class_admin','admin-password')
        body=dict(name='  新班级  ',grade=7,rows=6,cols=7)
        assert c.post('/api/v1/classes',json=body).status_code==401
        assert c.post('/api/v1/classes',json=body,headers=lh).status_code==403
        assert c.post('/api/v1/classes',json={**body,'name':'   '},headers=th).status_code==422
        r=c.post('/api/v1/classes',json=body,headers=th)
        assert r.status_code==201,r.text
        cid=r.json()['id']; assert r.json()['name']=='新班级'
        classes=c.get('/api/v1/classes',headers=th).json()
        assert any(row['id']==cid and row['seat_layout']==dict(rows=6,cols=7) for row in classes)
        assert c.patch('/api/v1/classes/'+cid,json=dict(name='越权'),headers=oh).status_code==403
        assert c.patch('/api/v1/classes/'+foreign_id,json=dict(name='跨校'),headers=ah).status_code==403
        assert c.patch('/api/v1/classes/'+cid,json=dict(name='只读'),headers=lh).status_code==403
        existing=next(row for row in classes if row['size']>0)
        assert c.patch('/api/v1/classes/'+existing['id'],json=dict(rows=1,cols=1),headers=th).status_code==409
        assert c.patch('/api/v1/classes/'+cid,json=dict(rows=21),headers=th).status_code==422
        r=c.patch('/api/v1/classes/'+cid,json=dict(name='修改后的班级',grade=8,rows=5,cols=5),headers=ah)
        assert r.status_code==200,r.text
        assert r.json()['seat_layout']==dict(rows=5,cols=5)

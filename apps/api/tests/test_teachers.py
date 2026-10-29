import os
import tempfile

os.environ.setdefault("MATHMATE_DB_URL", "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="mathmate-teachers-"), "test.db"))

from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.db import SessionLocal
from app.models import Teacher, Class
from app.auth import hash_password
from app.seed import seed


def test_teacher_management_boundaries():
    seed(reset=True)
    with SessionLocal() as db:
        db.add(Teacher(username='admin_test', name='管理员', role='school_admin', password_hash=hash_password('admin-password')))
        foreign = Class(name='其他学校', grade=8, tenant_id='other-school')
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    with TestClient(app) as c:
        def login(u,p):
            r=c.post('/api/v1/auth/login',json=dict(username=u,password=p))
            assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['access_token']}
        th=login('teacher','mathmate')
        ah=login('admin_test','admin-password')
        assert c.get('/api/v1/teachers',headers=th).status_code==403
        data=dict(username='new_teacher', name='新老师', password='initial-password', class_ids=[foreign_id])
        assert c.post('/api/v1/teachers',headers=ah,json=data).status_code==422
        data['class_ids']=[]
        r=c.post('/api/v1/teachers',headers=ah,json=data)
        assert r.status_code==201,r.text
        tid=r.json()['id']
        assert 'password_hash' not in r.json()
        assert c.post('/api/v1/teachers',headers=ah,json=data).status_code==409
        nh=login('new_teacher','initial-password')
        assert c.post('/api/v1/auth/password',headers=nh,json=dict(old_password='incorrect',password='replacement')).status_code==400
        assert c.post('/api/v1/teachers/'+tid+'/password',headers=ah,json=dict(password='replacement')).status_code==204
        assert c.get('/api/v1/auth/me',headers=nh).status_code==401
        nh=login('new_teacher','replacement')
        data.pop('password')
        data.update(role='teacher',status='disabled')
        assert c.put('/api/v1/teachers/'+tid,headers=ah,json=data).status_code==200
        assert c.get('/api/v1/auth/me',headers=nh).status_code==401
        me=c.get('/api/v1/auth/me',headers=ah).json()
        assert c.put('/api/v1/teachers/'+me['id'],headers=ah,json=dict(name='管理员',username='admin_test',role='teacher')).status_code==409
        assert c.post('/api/v1/auth/password',headers=th,json=dict(old_password='mathmate',password='new-password')).status_code==204
        assert c.get('/api/v1/auth/me',headers=th).status_code==401

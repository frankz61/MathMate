"""Transactional management audit; credential material is never recorded."""
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from .models import AuditLog, Teacher, TeacherClass, Class, Student, ScreenDevice

FIELDS = {
 Teacher: ('name', 'username', 'role', 'status', 'token_version'),
 TeacherClass: ('teacher_id', 'class_id'),
 Class: ('name', 'grade', 'seat_layout'),
 Student: ('name', 'student_no', 'seat_no', 'status', 'class_id'),
 ScreenDevice: ('name', 'status', 'class_id'),
}

@event.listens_for(Session, 'after_flush')
def management_audit(db, context):
 actor = db.info.get('actor')
 if not actor:
  return
 for action, objects in [('create', list(db.new)), ('update', list(db.dirty)), ('delete', list(db.deleted))]:
  for obj in objects:
   fields = FIELDS.get(type(obj))
   if not fields:
    continue
   state = inspect(obj)
   changes = {}
   for key in fields:
    history = state.attrs[key].history
    if action != 'update' or history.has_changes():
     changes[key] = {'before': history.deleted[0] if history.deleted else None,
                     'after': getattr(obj, key) if action != 'delete' else None}
   if isinstance(obj, Teacher) and state.attrs.password_hash.history.has_changes():
    changes['password_changed'] = True
   if not changes:
    continue
   db.add(AuditLog(tenant_id=actor[2], actor_id=actor[0], actor_name=actor[1], action=action,
     object_type=obj.__tablename__, object_id=getattr(obj, 'id', None) or getattr(obj, 'teacher_id', ''), changes=changes))

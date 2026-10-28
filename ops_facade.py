"""Per-request adapter lets the existing workspace use authenticated services."""
import uuid
import streamlit as st
import ops_auth as auth
import ops_service as api
import user_service as rules


class WorkspaceService:
    guarded=True
    week_start=staticmethod(rules.week_start)
    now_london=staticmethod(rules.now_london)
    today_london=staticmethod(rules.today_london)
    attendance=staticmethod(rules.attendance)

    def __init__(self,token,coach_id=None):
        self.token=token
        self.user=auth.current_user(token)
        self.coach_id=self.user['coach_id'] if self.user['role']=='coach' else coach_id
        self.can_edit_students=self.user['role']!='coach' or api.COACH_CAN_EDIT_STUDENTS
        self.can_manage_courses=self.user['role']=='system_admin' or (self.coach_id is not None and self.coach_id==self.user['coach_id'])
        import ops_store
        from ops_credits import enabled
        with ops_store.connect() as con:
            self.approved_feedback_editable=enabled(con)
            from ops_credits import booking_enabled
            self.automatic_absence_debit=booking_enabled(con)

    def students(self,search=''):return api.list_students(self.token,search,self.coach_id)
    def student(self,sid):return api.student_detail(self.token,sid,self.coach_id)
    def lessons(self,start=None,sid=None):return api.list_lessons(self.token,start,sid,self.coach_id)
    def lesson(self,lid):return api.lesson_detail(self.token,lid)
    def teaching_access(self,p):
        target=self.coach_id if self.coach_id is not None else self.user['coach_id']
        return api.teaching_access(self.user,target) and (not p.get('id') or 'teaching_revision' in p)
    def feedback_access(self,l):return self.user['role']=='system_admin' or l['coach_id']==self.user['coach_id']

    def version(self,row,prefix):
        key=f'ops_version_{prefix}'
        return st.session_state.setdefault(key,{'revision':row.get('revision',0),'teaching_revision':row.get('teaching_revision',-1)})

    def save_student(self,name,contact='',source='',level='',pain_points='',goals='',remarks='',joined_at=None,sid=None,expected_revision=None):
        version=expected_revision or {}
        return api.save_student(self.token,name,contact,source,level,pain_points,goals,remarks,joined_at,sid,self.coach_id,version.get('revision'),version.get('teaching_revision',-1))
    def set_star(self,sid,value):return api.set_star(self.token,sid,value)
    def check_in(self,lid,note,goal,expected_revision):return api.check_in(self.token,lid,note,goal,expected_revision['revision'])
    def cancel(self,lid,reason,advance,detail,expected_revision):return api.cancel(self.token,lid,reason,advance,detail,expected_revision['revision'])
    def reschedule(self,lid,day,begin,end,place,reason,expected_revision):return api.reschedule(self.token,lid,day,begin,end,place,reason,expected_revision['revision'])
    def archive_lesson(self,lid,expected_revision):return api.archive_lesson(self.token,lid,expected_revision['revision'])
    def clear_feedback(self,lid,expected_revision):return api.clear_feedback(self.token,lid,expected_revision['revision'])
    def create_lesson(self,sid,kind,start,end,location):
        key=st.session_state.setdefault('ops_create_request',uuid.uuid4().hex)
        lid=api.create_lesson(self.token,sid,kind,start,end,location,self.coach_id,key)
        st.session_state.pop('ops_create_request',None)
        return lid
    def daily_note(self,day):
        result=api.daily_note(self.token,day)
        st.session_state.setdefault(f'ops_note_version_{day}',result['revision'])
        return result
    def save_daily_note(self,day,body):
        key=f'ops_note_version_{day}'
        api.save_daily_note(self.token,day,body,st.session_state.get(key,-1))
        st.session_state[key]=api.daily_note(self.token,day)['revision']
    def import_bookings(self,rows):
        from ops_import import process
        return process(self.token,rows,self.coach_id,True,template='xiaoluo')
    def preview_import(self,rows):
        from ops_import import process
        return process(self.token,rows,self.coach_id,False,template='xiaoluo')
    def can_import_sheet(self):
        from ops_import import sheet_target
        if self.coach_id is None:return False
        return sheet_target(self.token,self.coach_id)

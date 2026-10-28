"""Authenticated phase-1 preview. Legacy entry points are not loaded."""
# Preserve the previous UI until the explicit version-5 migration is complete.
import ops_store as _ui_store
import streamlit as _ui_st
_new_ui=False
if _ui_store.available():
    try:
        with _ui_store.connect() as _ui_con:
            _new_ui=_ui_con.execute('SELECT 1 FROM ops_migrations WHERE version=5').fetchone() is not None
    except Exception:pass
if _new_ui:
    from ops_ui_app import run as _ui_run
    _ui_run()
    _ui_st.stop()

from datetime import timedelta
import streamlit as st

import ops_store as store
import ops_auth as auth
import ops_service as svc
from user_service import week_start, attendance
from user_theme import apply_theme
from ops_facade import WorkspaceService
import user_views as workspace
import ops_reviews as reviews
from ops_review_ui import notice as review_notice,render_management

st.set_page_config(page_title='Jamie Tennis',page_icon='🎾',layout='wide',initial_sidebar_state='collapsed')
apply_theme()
st.title('Jamie Tennis')
st.caption('教学工作台 · 反馈提交与运营审核')
navigation=st.empty()
content=st.empty()

# Migration is an explicit local operation, never a side effect of an HTTP request.
if not store.available():
    st.info('请先运行“创建阶段一管理员.bat”，建立独立预览数据库。')
    st.stop()
try:
    with store.connect() as con:
        ready=con.execute('SELECT 1 FROM ops_migrations WHERE version=3').fetchone()
except Exception:
    ready=False
if not ready:
    st.error('请先关闭预览，运行“升级反馈审核.bat”，保留原账号和数据完成升级。')
    st.stop()


def reset_actor_state():
    for key in list(st.session_state):
        if key.startswith(('ops_','review_','daily_note_','notes_','slot_','user_','week_','student_table_','week_table_')) or key in ('active_dialog','dialog_active','dialog_notice','table_epoch'):
            st.session_state.pop(key,None)


def login_form():
    with content.container():
        st.subheader('登录')
        with st.form('ops_login',clear_on_submit=True):
            username=st.text_input('登录账号',max_chars=64)
            password=st.text_input('密码',type='password',max_chars=128)
            submitted=st.form_submit_button('登录',type='primary')
        if submitted:
            try:token=auth.login(username,password)
            except (ValueError,RuntimeError) as error:st.error(str(error))
            else:
                reset_actor_state()
                st.session_state['ops_session']=token
                content.empty()


token=st.session_state.get('ops_session')
if token:
    try:user=auth.current_user(token)
    except auth.AuthenticationError:
        reset_actor_state()
        token=None
        st.info('登录已失效，请重新登录。')
if not token:
    login_form()
    token=st.session_state.get('ops_session')
    if not token:st.stop()
    user=auth.current_user(token)

role_names={'coach':'教练','operator':'运营管理员兼教练','system_admin':'系统管理员'}
with navigation.container():
    st.write(f"{user['display_name']} · {role_names[user['role']]}")
    review_notice(token)
    pages=['本周课程','学员档案','课程管理','课表导入','账号安全']
    if user['role'] in ('operator','system_admin'):pages.insert(3,'课包管理')
    if user['role']=='system_admin':pages+=['账号管理','操作记录']
    page=st.radio('导航',pages,horizontal=True,key='ops_navigation')
    if st.button('退出登录'):
        try:auth.logout(token)
        finally:
            reset_actor_state()
            st.rerun()


@st.fragment(run_every=30)
def refresh_clock():
    try:auth.current_user(token)
    except auth.AuthenticationError:
        st.rerun()
    minute=rules_now().strftime('%Y-%m-%d %H:%M')
    previous=st.session_state.get('ops_clock_minute')
    st.session_state['ops_clock_minute']=minute
    stats=reviews.inbox_stats(token)
    signature=tuple(stats.values())
    old_signature=st.session_state.get('ops_inbox_signature')
    st.session_state['ops_inbox_signature']=signature
    reading_review=page=='课程管理' and st.session_state.get('review_management_view')=='反馈审核' and st.session_state.get('review_opened') is not None
    if ((previous and previous!=minute) or (old_signature is not None and old_signature!=signature)) and not st.session_state.get('dialog_active') and not reading_review:
        st.rerun()


from user_service import now_london as rules_now
refresh_clock()

with content.container():
    notice=st.session_state.pop('user_notice',None)
    if notice:st.success(notice)
    try:
        if page=='课包管理':
            from ops_credit_ui import render
            render(token)
        elif page=='课程管理' and user['role'] in ('operator','system_admin'):
            render_management(token)
        elif page in ('本周课程','学员档案','课程管理','课表导入'):
            options=svc.coaches(token)
            coach_id=user['coach_id']
            if user['role']!='coach':
                mapping={p['id']:p['name'] for p in options}
                choices=[0]+list(mapping)
                coach_id=st.selectbox('查看 / 管理哪位教练',choices,
                    index=choices.index(user['coach_id']) if user['coach_id'] in choices else 0,
                    format_func=lambda cid:mapping.get(cid,'全部教练（新增或导入前请选择具体教练）'),key='ops_coach_filter')
                coach_id=coach_id or None
            context=(user['id'],page,coach_id)
            if st.session_state.get('ops_context')!=context:
                workspace.reset_selection()
                st.session_state['ops_context']=context
            service=WorkspaceService(token,coach_id)
            main,right=st.columns([3.2,1.05],gap='large')
            with main:
                if page=='本周课程':workspace.weekly_page(service)
                elif page=='学员档案':
                    workspace.students_page(service)
                    if user['role']!='coach':
                        with st.expander('将学员对接给教练'):
                            people=svc.list_students(token)
                            people_map={p['id']:p['name'] for p in people}
                            teacher_map={p['id']:p['name'] for p in options}
                            if people_map and teacher_map:
                                with st.form('ops_link_student'):
                                    sid=st.selectbox('对接的学员',list(people_map),format_func=people_map.get)
                                    cid=st.selectbox('对接的教练',list(teacher_map),format_func=teacher_map.get)
                                    linked=st.form_submit_button('确认对接')
                                if linked:
                                    svc.link_student(token,sid,cid)
                                    st.success('已对接。教练可以看到公共资料，并维护自己的私有教学档案。')
                elif page=='课程管理':workspace.management_page(service)
                else:
                    from user_upload_page import render_upload_page
                    render_upload_page(service)
            with right:
                if page=='本周课程':workspace.upcoming_cards(service)
                workspace.sticky_notes(service)
            workspace.dispatch_dialog(service)
        elif page=='账号安全':
            st.subheader('修改自己的密码')
            with st.form('ops_password',clear_on_submit=True):
                old=st.text_input('当前密码',type='password')
                new=st.text_input('新密码（15–128 字符）',type='password')
                again=st.text_input('再次输入新密码',type='password')
                saved=st.form_submit_button('修改密码并退出登录')
            if saved:
                if new!=again:st.error('两次新密码不一致。')
                else:
                    auth.change_password(token,old,new)
                    reset_actor_state();st.rerun()
        elif page=='账号管理':
            st.subheader('账号管理')
            accounts=auth.list_accounts(token)
            st.dataframe([{**a,'role':role_names[a['role']]} for a in accounts],hide_index=True,width='stretch')
            with st.expander('新增账号'):
                with st.form('ops_new_account',clear_on_submit=True):
                    username=st.text_input('新账号')
                    name=st.text_input('显示名称')
                    role=st.selectbox('角色',list(role_names),format_func=role_names.get)
                    password=st.text_input('初始密码（15–128 字符）',type='password')
                    saved=st.form_submit_button('创建账号')
                if saved:
                    auth.create_account(token,username,name,password,role)
                    st.success('账号已创建。');st.rerun()
            with st.form('ops_active'):
                mapping={a['id']:a for a in accounts}
                aid=st.selectbox('选择账号',list(mapping),format_func=lambda i:mapping[i]['display_name'])
                active=st.checkbox('设为启用状态',value=True)
                saved=st.form_submit_button('保存账号状态')
            if saved:
                auth.set_active(token,aid,active)
                st.rerun()
        elif page=='操作记录':
            st.subheader('最近 200 条操作记录')
            st.dataframe(svc.audit_log(token),hide_index=True,width='stretch')
    except auth.AuthenticationError:
        reset_actor_state();st.rerun()
    except (ValueError,RuntimeError) as error:
        st.error(str(error))

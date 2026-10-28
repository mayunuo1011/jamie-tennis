"""Version-5 workspace. Reuses existing authenticated services and forms."""
from datetime import datetime,timedelta,time
from html import escape
import streamlit as st
import pandas as pd
import ops_auth as auth
import ops_service as svc
import ops_reviews as reviews
import ops_store as store
import ops_credits as credits
import ops_ui_service as api
from ops_facade import WorkspaceService
from user_theme import apply_theme
import user_service as rules
import user_views as views
from ops_ui_dialogs import open_dialog,close_dialog,lesson_dialog,review_dialog,group_form,group_dialog,selected_row
import ops_ui_credits

ROLES={'coach':'教练','operator':'运营管理员兼教练','system_admin':'系统管理员'}


def clear_actor():
    for key in list(st.session_state):
        if key.startswith(('ops_','user_','review_','daily_note_','notes_','slot_','week_','student_table_','week_table_')) or key in ('active_dialog','dialog_active','dialog_notice','table_epoch'):
            st.session_state.pop(key,None)


def welcome_closed():st.session_state['ops_ui_welcome_seen']=True


def go_review():
    welcome_closed();close_dialog();st.session_state['ops_navigation']='打卡审核'


@st.dialog('待办提醒',width='small',on_dismiss=welcome_closed)
def welcome_dialog(count):
    st.write(f'你有 {count} 节课等待审核。')
    a,b=st.columns(2)
    if a.button('去打卡审核',type='primary',key='ops_ui_welcome_review',on_click=go_review):st.rerun()
    if b.button('知道了',key='ops_ui_welcome_close'):
        welcome_closed();st.rerun()


@st.fragment(run_every=30)
def todo_panel(token):
    user=auth.current_user(token)
    mine=svc.list_lessons(token,rules.week_start(),coach_id=user['coach_id']) if user['coach_id'] else []
    count=sum(rules.attendance(r)=='没上' for r in mine)
    count+=sum(datetime.fromisoformat(r['ends_at'])>rules.now_london().replace(tzinfo=None) for r in api.groups(token,rules.week_start(),user['coach_id'])) if user['coach_id'] else 0
    stats=reviews.inbox_stats(token)
    with st.container(border=True,key='ui_todo'):
        st.subheader('待办')
        st.write(f'本周我的待上课：{count} 节')
        if user['role'] in credits.ROLES:
            st.write(f"待审核：{stats['pending']} 节 · 未读 {stats['unread']} 条")
            ops_ui_credits.absence_todo(token)
            if stats['pending'] and st.button('处理待审核',key='ops_ui_todo_review',on_click=go_review):st.rerun(scope='app')
        else:
            pending=sum(rules.attendance(r)=='上了' and not r['checkin_done'] for r in mine)
            st.write(f'待打卡：{pending} 节')
            if stats['returned']:st.warning(f"有{stats['returned']}条反馈被退回。")


def status(row):
    if row['status'] in ('已取消','已改期'):return row['status']
    if row.get('review_state')=='approved':return '已完成'
    if rules.attendance(row)=='没上':return '没上'
    return '待审核' if row['checkin_done'] else '未打卡'


def upcoming_panel(token,service):
    now=rules.now_london().replace(tzinfo=None)
    rows=[{'name':r['student_name']+' · '+r['kind'],'start':r['starts_at'],'end':r['ends_at'],'location':r['location']} for r in service.lessons() if r['status'] not in ('已取消','已改期') and datetime.fromisoformat(r['ends_at'])>now]
    rows += [{'name':r['name']+' · 团课','start':r['starts_at'],'end':r['ends_at'],'location':r['location']} for r in api.groups(token,coach_id=service.coach_id) if datetime.fromisoformat(r['ends_at'])>now]
    with st.container(border=True,key='ui_upcoming'):
        st.subheader('接下来的课程')
        for row in sorted(rows,key=lambda r:r['start'])[:3]:
            st.write(row['name']);st.caption(row['start'].replace('T',' ')+'–'+row['end'][11:16]+' · '+row['location'])
        if not rows:st.caption('暂无接下来的课程。')


def weekly(token,service):
    st.subheader('本周课程')
    start=views.week_controls();st.write(f'{start} — {start+timedelta(days=6)}')
    records=sorted(service.lessons(start),key=lambda r:(r['starts_at'],r['id']))
    groups=api.groups(token,start,service.coach_id)
    a,b=st.columns(2)
    a.metric('待上课',sum(rules.attendance(r)=='没上' for r in records)+sum(datetime.fromisoformat(r['ends_at'])>rules.now_london().replace(tzinfo=None) for r in groups))
    b.metric('待打卡',sum(rules.attendance(r)=='上了' and not r['checkin_done'] for r in records))
    listing,actions,exports=st.tabs(['课程清单','课后管理 · 取消 / 改期','导出课后记录'])
    with listing:
        pick=st.selectbox('状态筛选',['全部','没上','未打卡','已完成'],key='ops_ui_week_status')
        combined=[]
        for row in records:
            current=status(row)
            if pick!='全部' and current!=pick:continue
            combined.append((row['starts_at'],'lesson',row['id'],{'学员ID':f"S{row['student_id']:03d}",'学员':row['student_name'],'课程ID':f"L{row['id']}",'教练':row['coach_name'],'类型':row['kind'],'日期':row['starts_at'][:10],'时间':row['starts_at'][11:16]+'–'+row['ends_at'][11:16],'地点':row['location'],'状态':current}))
        for row in groups:
            current='没上' if datetime.fromisoformat(row['ends_at'])>rules.now_london().replace(tzinfo=None) else '已上课（团课）'
            if pick!='全部' and current!=pick:continue
            combined.append((row['starts_at'],'group',row['id'],{'学员ID':'—','学员':row['name'],'课程ID':f"G{row['id']}",'教练':row['coach_name'],'类型':'团课','日期':row['starts_at'][:10],'时间':row['starts_at'][11:16]+'–'+row['ends_at'][11:16],'地点':row['location'],'状态':current}))
        combined.sort(key=lambda r:(r[0],r[1],r[2]))
        absence={r['id']:r['absence_count'] for r in records}
        table_rows=[{**r[3],'缺课提醒':f"累计 {absence[r[2]]} 次" if r[1]=='lesson' and absence[r[2]]>=2 else ''} for r in combined]
        frame=pd.DataFrame(table_rows)
        event=st.dataframe(frame.style.apply(views.highlight,axis=1),hide_index=True,width='stretch',on_select='rerun',selection_mode='single-cell',key=f"ops_ui_week_table_{service.coach_id}_{pick}_{start}_{st.session_state.get('table_epoch',0)}")
        st.caption('点击课程行查看反馈、取消或改期。已完成仅指审核完成；待审核仍保留在默认清单。')
        index=selected_row(event)
        if index is not None and index<len(combined):
            _,kind,lid,_=combined[index];open_dialog(kind,lid)
        if service.can_manage_courses:
            with st.expander('＋ 手动添加私教 / 陪练'):
                people=service.students()
                if people:
                    labels={p['id']:f"S{p['id']:03d} · {p['name']}" for p in people}
                    with st.form('ops_ui_add_lesson'):
                        sid=st.selectbox('学员',list(labels),format_func=labels.get)
                        kind=st.selectbox('课程类型',['私教','陪打'])
                        day=st.date_input('日期',value=rules.today_london())
                        a,b=st.columns(2);begin=a.time_input('开始',value=time(14));end=b.time_input('结束',value=time(15))
                        location=st.text_input('地点',value='Meadows')
                        saved=st.form_submit_button('添加课程',type='primary')
                    if saved:
                        service.create_lesson(sid,kind,datetime.combine(day,begin).isoformat(timespec='minutes'),datetime.combine(day,end).isoformat(timespec='minutes'),location)
                        views.flash('课程已添加。')
                else:st.info('先建立学员并由 Jamie 录入课时。')
            if service.user['role'] in credits.ROLES and service.user['coach_id'] is not None and service.coach_id==service.user['coach_id']:
                with st.expander('＋ 添加团课提醒'):group_form(token)
    with actions:
        if service.can_manage_courses:views.management_page(service,start=start,show_heading=False)
        else:st.info('仅能查看其他教练的安排。取消、改期由任课教练处理；系统管理员可执行有记录的维护。')
    with exports:
        st.subheader('导出课后记录')
        views.export_panel(records,start)


def students_page(token,service):
    st.subheader('学员档案')
    query=st.text_input('搜索学员',placeholder='称呼或学员ID',key='ops_ui_student_search')
    people=service.students(query);st.caption(f'共 {len(people)} 位学员')
    if service.user['role'] in credits.ROLES:rows=ops_ui_credits.student_rows(token,people)
    else:rows=[{'学员ID':f"S{p['id']:03d}",'称呼':p['name'],'加入时间':p['joined_at'] or '待补充','水平':p.get('level') or '待补充'} for p in people]
    event=st.dataframe(rows,hide_index=True,width='stretch',on_select='rerun',selection_mode='single-cell',key=f"ops_ui_students_{service.coach_id}_{query}_{st.session_state.get('table_epoch',0)}")
    index=selected_row(event)
    if index is not None and index<len(people):open_dialog('profile',people[index]['id'])
    if service.user['role'] in credits.ROLES and people:
        with st.expander('对接私教 / 陪练教练'):
            teachers=svc.coaches(token);names={p['id']:f"S{p['id']:03d} · {p['name']}" for p in people};teacher_names={c['id']:c['name'] for c in teachers}
            with st.form('ops_ui_link_student'):
                sid=st.selectbox('学员',list(names),format_func=names.get)
                cid=st.selectbox('对接教练',list(teacher_names),format_func=teacher_names.get)
                linked=st.form_submit_button('确认对接')
            if linked:svc.link_student(token,sid,cid);views.flash('已对接私教 / 陪练教练。')
            st.caption('不用于团课分班，不会改变课时归属。')


def review_page(token):
    auth.current_user(token)
    st.subheader('打卡审核')
    start=views.week_controls();st.caption(f'{start} — {start+timedelta(days=6)} · 所有教练的已上私教 / 陪练')
    records=api.review_courses(token,start)
    rows=[{'学员ID':f"S{r['student_id']:03d}",'学员':r['student_name'],'教练':r['coach_name'],'类型':r['kind'],'日期':r['starts_at'][:10],'时间':r['starts_at'][11:16]+'–'+r['ends_at'][11:16],'地点':r['location'],'打卡':'已打卡' if r['checkin_done'] else '未打卡','审核':reviews.STATES.get(r.get('review_state'),'未提交')} for r in records]
    event=st.dataframe(rows,hide_index=True,width='stretch',on_select='rerun',selection_mode='single-cell',key=f"ops_ui_review_table_{start}_{st.session_state.get('table_epoch',0)}")
    st.caption('点击已打卡课程查看反馈并审核；未打卡课程暂不能审核。已完成的可撤销。')
    index=selected_row(event)
    if index is not None and index<len(records):open_dialog('review',records[index]['id'])


def account_page(token,page):
    if page=='账号安全':
        st.subheader('账号安全')
        with st.form('ops_ui_password',clear_on_submit=True):
            old=st.text_input('当前密码',type='password');new=st.text_input('新密码（15–128字符）',type='password');again=st.text_input('确认新密码',type='password')
            saved=st.form_submit_button('修改密码并退出登录')
        if saved:
            if new!=again:st.error('两次密码不一致。')
            else:auth.change_password(token,old,new);clear_actor();st.rerun()
    elif page=='操作记录':
        st.subheader('操作记录');st.dataframe(svc.audit_log(token),hide_index=True,width='stretch')
    else:
        st.subheader('账号管理');accounts=auth.list_accounts(token)
        st.dataframe(accounts,hide_index=True,width='stretch')
        with st.expander('新增账号'):
            with st.form('ops_ui_account_new'):
                username=st.text_input('账号');name=st.text_input('显示名称');role=st.selectbox('角色',list(ROLES),format_func=ROLES.get);password=st.text_input('初始密码',type='password')
                saved=st.form_submit_button('创建账号')
            if saved:auth.create_account(token,username,name,password,role);views.flash('账号已建立。')
        with st.form('ops_ui_account_active'):
            names={a['id']:a['display_name'] for a in accounts}
            aid=st.selectbox('账号',list(names),format_func=names.get);active=st.checkbox('启用',value=True);saved=st.form_submit_button('保存账号状态')
        if saved:auth.set_active(token,aid,active);st.rerun()


def run():
    st.set_page_config(page_title='Jamie Tennis',page_icon='🎾',layout='wide',initial_sidebar_state='expanded')
    apply_theme()
    st.markdown('''<style>
    [data-testid="stSidebarCollapsedControl"],[data-testid="stExpandSidebarButton"]{visibility:visible!important}
    .block-container{padding-top:5.4rem}
    .st-key-ui_todo{border-top:3px solid #c9344c!important;background:#fff6f7}
    [data-testid="stSidebar"] .st-key-ui_todo p{color:#173b65!important}
    [data-testid="stSidebar"] .st-key-ui_todo h3{color:#173b65!important}
    [data-testid="stSidebar"] .st-key-notes_panel{position:static;width:100%;max-height:none}
    [data-testid="stSidebar"] .st-key-notes_panel p,[data-testid="stSidebar"] .st-key-notes_panel span,[data-testid="stSidebar"] .st-key-notes_panel label{color:#173b65}
    .st-key-ui_upcoming{border-top:3px solid #173b65!important;background:#f0f5fc}
    [data-testid="stTabs"] [role="tab"]{font-weight:650;min-height:48px;padding:.8rem 1rem}
    [data-testid="stSidebar"] [data-testid="stButton"] button{width:100%}
    [data-testid="stSidebar"] [data-testid="stButton"] p{color:#173b65}
    @media(max-width:650px){.workspace-title{position:static;text-align:left;margin-bottom:1rem}.block-container{padding-top:2rem}}
    </style>''',unsafe_allow_html=True)
    token=st.session_state.get('ops_session')
    user=None
    if token:
        try:user=auth.current_user(token)
        except auth.AuthenticationError:clear_actor();token=None
    if not token:
        st.title('Jamie Tennis')
        with st.form('ops_ui_login',clear_on_submit=True):
            username=st.text_input('登录账号');password=st.text_input('密码',type='password');saved=st.form_submit_button('登录',type='primary')
        if saved:
            try:
                new_token=auth.login(username,password);clear_actor();st.session_state['ops_session']=new_token;st.rerun()
            except (ValueError,RuntimeError) as error:st.error(str(error))
        return
    st.markdown(f'<div class="workspace-title"><strong>{escape(user["display_name"])}的教学工作台</strong><small>JAMIE TENNIS</small></div>',unsafe_allow_html=True)
    pages=['本周课程']
    with store.connect() as con:
        if credits.booking_enabled(con):pages+=['开放预约']
    pages+=['学员档案']
    if user['role'] in credits.ROLES:pages+=['打卡审核','课包管理']
    if user['role']=='system_admin':pages+=['账号管理','操作记录']
    pages+=['账号安全']
    if st.session_state.get('ops_navigation') not in pages:st.session_state['ops_navigation']='本周课程'
    with st.sidebar:
        st.title('Jamie Tennis')
        page=st.radio('导航',pages,key='ops_navigation',on_change=close_dialog)
        if page!='开放预约':st.session_state.pop('ops_slot_cell',None)
        todo_panel(token)
    message=st.session_state.pop('user_notice',None)
    if message:st.success(message)
    cid=user['coach_id']
    if page in ('本周课程','学员档案','课表导入') and user['role'] in credits.ROLES:
        teachers={c['id']:c['name'] for c in svc.coaches(token)}
        choices=[0]+list(teachers)
        selected=st.selectbox('选择教练',choices,index=0 if page=='学员档案' else choices.index(cid) if cid in choices else 0,format_func=lambda i:teachers.get(i,'全部教练'),key='ops_ui_student_coach_filter' if page=='学员档案' else 'ops_ui_coach_filter',on_change=close_dialog)
        cid=selected or None
    service=WorkspaceService(token,cid)
    service.profile_read_only=user['role'] in credits.ROLES
    if page=='开放预约':
        main=st.container();right=None
        with st.sidebar:views.sticky_notes(service)
    else:main,right=st.columns([3.3,1.1],gap='large')
    with st.sidebar:
        st.divider()
        if st.button('退出登录',key='ops_ui_logout'):
            auth.logout(token);clear_actor();st.rerun()
    try:
        with main:
            if page=='本周课程':weekly(token,service)
            elif page=='学员档案':students_page(token,service)
            elif page=='打卡审核':review_page(token)
            elif page=='课包管理':ops_ui_credits.render(token)
            elif page=='开放预约':
                import ops_booking_ui
                ops_booking_ui.render(token)
            elif page=='课表导入':
                from user_upload_page import render_upload_page
                render_upload_page(service)
            else:account_page(token,page)
        if right is not None:
            with right:
                if page=='本周课程':
                    upcoming_panel(token,service)
                views.sticky_notes(service)
        if user['role'] in credits.ROLES and not st.session_state.get('ops_ui_welcome_seen'):
            pending=reviews.inbox_stats(token)['pending']
            if pending:
                welcome_dialog(pending);return
            welcome_closed()
        active=st.session_state.get('ops_ui_dialog')
        if active:
            kind,identifier=active
            if kind=='lesson':lesson_dialog(token,identifier)
            elif kind=='review':review_dialog(token,identifier)
            elif kind=='credit':ops_ui_credits.purchase_dialog(token,identifier)
            elif kind=='group':group_dialog(token,identifier)
            elif kind=='profile':views.profile_dialog(identifier,service)
    except auth.AuthenticationError:
        clear_actor();st.rerun()
    except (ValueError,RuntimeError) as error:st.error(str(error))

"""Student list -> purchase dialog -> history with explicit confirmation."""
import uuid
import streamlit as st
import ops_auth as auth
import ops_service as svc
import ops_credits as credits
import ops_ui_service as api
import ops_store as store
import user_service as rules
from ops_ui_dialogs import open_dialog,close_dialog,success,selected_row


def student_rows(token,people):
    return [{'学员ID':f"S{p['id']:03d}",'称呼':p['name'],'加入时间':p['joined_at'] or '待补充',
             '对接教练':api.student_summary(token,p['id'])['coaches'],
             '已购课程':api.student_summary(token,p['id'])['purchases'],
             '课程余量':api.student_summary(token,p['id'])['balance'],
             '所在班级 / 类型':api.student_summary(token,p['id'])['classes']} for p in people]


def render(token):
    auth.current_user(token)
    credits.balances(token)  # Enforce role + schema on the server.
    st.subheader('课包管理')
    st.caption('新学员与课时一起录入；已有学员点击名单续课或查看记录。')
    with st.expander('＋ 新学员 · 录入课时'):
        registration_form(token)
    search=st.text_input('搜索学员',placeholder='称呼或学员ID',key='ops_ui_credit_search')
    people=svc.list_students(token,search)
    event=st.dataframe(student_rows(token,people),hide_index=True,width='stretch',on_select='rerun',selection_mode='single-cell',key=f"ops_ui_credit_table_{st.session_state.get('table_epoch',0)}")
    index=selected_row(event)
    if index is not None and index<len(people):open_dialog('credit',people[index]['id'])


@st.dialog('录入课时 / 续课记录',width='large',on_dismiss=close_dialog)
def purchase_dialog(token,sid):
    # Public profile access + financial permission are revalidated on every run.
    person=svc.student_detail(token,sid)
    rows=credits.packages(token,sid)
    st.subheader(f"{person['name']} · S{sid:03d}")
    st.write('当前课时：'+api.student_summary(token,sid)['balance'])
    with store.connect() as con:
        auth.require(con,token,credits.ROLES)
        if credits.booking_enabled(con):
            scopes=con.execute('SELECT DISTINCT p.coach_id,c.name coach_name,p.kind FROM packages p JOIN coaches c ON c.id=p.coach_id WHERE student_id=?',(sid,)).fetchall()
            st.caption('；'.join(f"{r['coach_name']}·{r['kind']}：预约占用{credits.occupied(con,sid,r['coach_id'],r['kind'])}节，可预约{max(0,credits.available(con,sid,r['coach_id'],r['kind']))}节" for r in scopes))
    teachers={c['id']:c['name'] for c in svc.coaches(token)}
    key_name=f'ops_ui_purchase_request_{sid}'
    request=st.session_state.setdefault(key_name,uuid.uuid4().hex)
    with st.form(f'ops_ui_purchase_{sid}',clear_on_submit=True):
        a,b=st.columns(2)
        cid=a.selectbox('任课教练',list(teachers),format_func=teachers.get,key=f'ops_ui_purchase_coach_{sid}')
        kind=b.selectbox('课程类型',['私教','陪打'],format_func=lambda k:'陪练' if k=='陪打' else k)
        a,b=st.columns(2)
        quantity=a.number_input('本次购买节数',min_value=1,max_value=10000,value=10,step=1)
        day=b.date_input('购买日期',value=rules.today_london())
        saved=st.form_submit_button('录入本次购课',type='primary')
    if saved:
        try:
            credits.add_package(token,sid,cid,kind,day,int(quantity),request)
            st.session_state.pop(key_name,None);success('本次购课已录入。')
        except (ValueError,RuntimeError) as error:st.error(str(error))
    st.write('以往购课 / 续课记录')
    if not rows:st.caption('暂无购买记录。')
    for p in rows:
        if p.get('is_debt'):
            st.caption(f"{p['coach_name']} · {p['kind']} · 缺课欠额 {p['remaining']}节（不是购课记录）")
            continue
        a,b=st.columns([4,1])
        a.write(f"{p['purchased_at']} · {p['coach_name'] or '归属待核对'} · {p['kind']} · 购{p['purchased']}节 · 余{p['remaining']}节"+(' · 已撤销' if p['voided'] else ''))
        can_undo=not p['voided'] and not p['used'] and p['coach_id'] is not None and p['purchased']==p['remaining']
        with b:
            with st.popover(f"撤销 P{p['id']}",disabled=not can_undo):
                st.warning(f"确定撤销这次录入吗？{p['purchased']}节将从对应余额移除，历史仍保留。")
                if st.button('确定撤销录入',key=f"ops_ui_void_{p['id']}"):
                    try:credits.void_package(token,p['id'],'用户确认撤销误录购课');success('本次录入已撤销。')
                    except (ValueError,RuntimeError) as error:st.error(str(error))
    st.caption('已使用的课包不能直接撤销。')
    with st.expander('课时变动明细 / 误扣退还'):
        history=credits.history(token,sid)
        st.dataframe([{'时间':r['created_at'],'教练':r['coach_name'],'类型':r['kind'],'变动':r['delta'],'操作':{'purchase':'购课','opening':'期初余额','void_purchase':'撤销购课','lesson':'上课扣费','absence':'缺课扣费','return':'退还'}[r['action']],'说明':r['reason']} for r in history],hide_index=True,width='stretch')
        with store.connect() as con:
            auth.require(con,token,credits.ROLES)
            refundable=[r for r in history if r['action'] in ('lesson','absence') and credits.active_charge(con,r['lesson_id']) and credits.active_charge(con,r['lesson_id'])['id']==r['id']]
        for item in refundable:
            with st.popover(f"退还 L{item['lesson_id']} 的1节"):
                reason=st.text_input('退还原因',key=f"ops_ui_refund_reason_{item['id']}")
                if st.button('确定退还1节',key=f"ops_ui_refund_{item['id']}"):
                    try:credits.return_credit(token,item['lesson_id'],reason);success('已退还1节，课程状态保留。')
                    except (ValueError,RuntimeError) as error:st.error(str(error))


def registration_form(token):
    teachers={c['id']:c['name'] for c in svc.coaches(token)}
    key='ops_register_request'
    request=st.session_state.setdefault(key,uuid.uuid4().hex)
    with st.form('ops_register_student',clear_on_submit=True):
        name=st.text_input('学员称呼 *')
        a,b=st.columns(2)
        cid=a.selectbox('任课教练',list(teachers),format_func=teachers.get,key='ops_register_coach')
        kind=b.selectbox('课程类型',['私教','陪打'],format_func=lambda k:'陪练' if k=='陪打' else k,key='ops_register_kind')
        a,b=st.columns(2)
        quantity=a.number_input('购买节数',min_value=1,max_value=10000,value=10,step=1)
        day=b.date_input('购买日期',value=rules.today_london(),key='ops_register_date')
        a,b=st.columns(2)
        contact=a.text_input('联系方式（选填）',key='ops_register_contact')
        source=b.text_input('学员来源（选填）',placeholder='微信、小红书、朋友推荐等')
        save=st.form_submit_button('添加学员并录入课时',type='primary')
    if save:
        try:sid=credits.register_student_package(token,name,cid,kind,day,int(quantity),request,contact,source)
        except (ValueError,RuntimeError) as error:st.error(str(error))
        else:
            st.session_state.pop(key,None)
            st.session_state['table_epoch']=st.session_state.get('table_epoch',0)+1
            st.session_state['user_notice']=f'已添加 S{sid:03d}，课时已录入并对接教练。'
            st.rerun()


def absence_todo(token):
    due=credits.pending_absences(token)
    with st.expander(f'缺课待处理（{len(due)}）'):
        if not due:st.caption('暂无待处理缺课。')
        for row in due:
            st.write(f"{row['student_name']} · {row['coach_name']} · {row['kind']}")
            st.caption(row['starts_at'].replace('T',' '))
            if st.button('处理缺课扣课',key=f"ops_ui_absence_{row['id']}"):
                try:credits.settle_absence(token,row['id'])
                except (ValueError,RuntimeError) as error:st.error(str(error))
                else:st.session_state['user_notice']='缺课已处理。';st.rerun(scope='app')

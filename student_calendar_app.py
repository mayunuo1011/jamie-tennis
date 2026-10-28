"""Two-navigation student portal. Staff login and passwords are unchanged."""
import uuid
import streamlit as st
import ops_store as store
import ops_credits as credits
import ops_auth as staff_auth
import ops_booking as api
import student_auth as auth
import booking_calendar as calendar


def clear():
    for key in list(st.session_state):
        if key.startswith(('student_','calendar_')):st.session_state.pop(key,None)


def close_dialog():calendar.reset()


def success(message):
    close_dialog();st.session_state['student_notice']=message;st.rerun()


@st.dialog('确定预约？',on_dismiss=close_dialog)
def book_dialog(token,slots):
    # Refresh the visible row before confirming, then book() validates again
    # under the same transaction that inserts the lesson.
    if len(slots)>1:
        sid=st.selectbox('选择教练 / 课程',[s['id'] for s in slots],format_func=lambda i:next(f"{s['coach_name']} · {s['kind']} · {s['location']}" for s in slots if s['id']==i))
        slot=next(s for s in slots if s['id']==sid)
    else:slot=slots[0]
    st.write(f"**{slot['coach_name']} · {slot['kind']}**")
    st.write(f"{slot['starts_at'].replace('T',' ')} — {slot['ends_at'][11:16]}")
    st.write(slot['location']);st.caption('确定后占用1节对应课时；课后经Jamie审核才正式扣课。')
    a,b=st.columns(2)
    if a.button('确定预约',type='primary',key='student_confirm_book'):
        request=st.session_state.setdefault(f"student_request_{slot['id']}",uuid.uuid4().hex)
        try:api.book(token,slot['id'],slot['revision'],request)
        except ValueError as error:st.error(str(error));st.button('关闭并刷新',on_click=close_dialog)
        else:
            st.session_state.pop(f"student_request_{slot['id']}",None);success('预约成功，可在“我的预约”查看。')
    if b.button('返回',key='student_back_book'):close_dialog();st.rerun()


@st.dialog('确定取消预约？',on_dismiss=close_dialog)
def cancel_dialog(token,row):
    st.write(f"{row['coach_name']} · {row['kind']} · {row['starts_at'].replace('T',' ')}")
    st.write('确定后释放本次课时占用，不计缺课。')
    a,b=st.columns(2)
    if a.button('确定取消',type='primary',key='student_confirm_cancel'):
        try:api.cancel_booking(token,row['id'],row['revision'])
        except ValueError as error:st.error(str(error));st.button('关闭并刷新',on_click=close_dialog)
        else:success('预约已取消，课时占用已释放。')
    if b.button('保留预约',key='student_keep_booking'):close_dialog();st.rerun()


def run():
    st.set_page_config(page_title='Jamie Tennis · 预约',page_icon='🎾',layout='wide')
    st.markdown('<style>[data-testid="stSidebar"] {background:#102c4c} [data-testid="stSidebar"] * {color:white} .block-container{padding-top:2rem} </style>',unsafe_allow_html=True)
    st.title('Jamie Tennis')
    if not store.available():st.info('请先安装并升级教练端。');return
    with store.connect() as con:
        if not credits.calendar_enabled(con):st.info('请先运行“升级预约日历版.bat”。');return
    token=st.session_state.get('student_session')
    if not token:
        st.subheader('进入预约')
        with st.form('student_alias_login'):
            alias=st.text_input('你的账号',placeholder='输入Jamie私发给你的账号')
            submit=st.form_submit_button('进入',type='primary')
        if submit:
            try:st.session_state['student_session']=auth.login(alias)
            except ValueError as error:st.error(str(error))
            else:st.rerun()
        return
    try:
        person=auth.current(token)
        with st.sidebar:
            st.title('Jamie Tennis')
            page=st.radio('导航',['课程预约','我的预约'],key='student_navigation',on_change=close_dialog)
            st.divider()
            if st.button('退出',key='student_logout'):
                auth.logout(token);clear();st.rerun()
        st.caption(f"你好，{person['name']}")
        notice=st.session_state.pop('student_notice',None)
        if notice:st.success(notice)
        if page=='课程预约':
            st.subheader('课程预约')
            start=calendar.controls('student_calendar')
            rows=api.calendar_slots(token,start)
            coach_ids=sorted({r['coach_id'] for r in rows})
            labels={r['coach_id']:r['coach_name'] for r in rows}
            coach=st.selectbox('教练',[0]+coach_ids,format_func=lambda i:labels.get(i,'全部教练'),key='student_coach',on_change=close_dialog)
            if coach:rows=[r for r in rows if r['coach_id']==coach]
            st.caption('绿色可预约 · 灰色不可预约／已约 · 蓝色为我的预约。手机可横向滑动查看整周。')
            if not rows:st.info('这一周还没有发布时段。')
            selected=calendar.render(rows,start,'student_calendar_table')
            available=[r for r in selected if r['state']=='可预约']
            if available:st.session_state['student_dialog']=('book',available)
            elif selected:st.caption('这个时段不能预约。请查看其他可预约时段。')
        else:
            st.subheader('我的预约')
            st.caption('提前至少24小时可自行取消；不足24小时，请微信联系教练。')
            rows=api.my_lessons(token)
            if not rows:st.info('还没有预约。')
            for row in rows:
                with st.container(border=True):
                    st.write(f"**{row['coach_name']} · {row['kind']}** · {row['status']}")
                    st.write(f"{row['starts_at'].replace('T',' ')}–{row['ends_at'][11:16]} · {row['location']}")
                    if row['can_cancel']:
                        if st.button('取消预约',key=f"student_cancel_{row['id']}"):st.session_state['student_dialog']=('cancel',row)
                    elif row['status']=='待上课':st.caption('不能自行取消，请微信联系教练。')
        active=st.session_state.get('student_dialog')
        if active:
            if active[0]=='book':book_dialog(token,active[1])
            else:cancel_dialog(token,active[1])
    except staff_auth.AuthenticationError:clear();st.rerun()
    except (ValueError,RuntimeError) as error:st.error(str(error))


if __name__=='__main__':run()

"""Minimal student booking UI. Uses the same persisted DB as the staff app."""
import uuid
import streamlit as st
import ops_auth as staff_auth
import student_auth as auth
import ops_booking as booking
import ops_store as store
import ops_credits as credits


def clear():
    for key in list(st.session_state):
        if key.startswith('student_'):st.session_state.pop(key,None)


def run_legacy():
    st.set_page_config(page_title='Jamie Tennis · 学员预约',page_icon='🎾',layout='centered')
    st.title('Jamie Tennis')
    if not store.available():
        st.info('请先完成教练端的安装和预约版本升级。');return
    with store.connect() as con:
        if not credits.booking_enabled(con):st.info('请先关闭教练端并运行“升级预约版.bat”。');return
    token=st.session_state.get('student_session')
    if not token:
        st.subheader('学员登录')
        st.caption('请使用 Jamie 发给你的学员 ID 和初始密码。')
        with st.form('student_login'):
            username=st.text_input('学员 ID',placeholder='例如 S001')
            password=st.text_input('密码',type='password')
            submit=st.form_submit_button('登录',type='primary')
        if submit:
            try:st.session_state['student_session']=auth.login(username,password)
            except ValueError as error:st.error(str(error))
            else:st.rerun()
        return
    try:
        person=auth.current(token)
        st.caption(f"{person['name']} · S{person['student_id']:03d}")
        if st.button('退出登录'):
            auth.logout(token);clear();st.rerun()
        if person['must_change']:
            st.info('首次登录，请先修改初始密码。修改后需要重新登录。')
            security(token);return
        notice=st.session_state.pop('student_notice',None)
        if notice:st.success(notice)
        reserve,mine,account=st.tabs(['预约课程','我的课程','账号安全'])
        with reserve:
            balances=booking.my_credits(token)
            if balances:st.dataframe([{'教练':r['coach_name'],'类型':r['kind'],'余额':r['balance'],'已预约占用':r['occupied'],'可预约':max(0,r['available'])} for r in balances],hide_index=True,width='stretch')
            st.caption('预约占用 1 节；上课、打卡并经 Jamie 审核后才正式扣课。取消或改期请联系教练。所有时间为英国伦敦当地时间。')
            slots=booking.open_slots(token)
            if not slots:st.info('暂时没有可预约时段，请联系教练。')
            for slot in slots:
                with st.container(border=True):
                    st.write(f"**{slot['coach_name']} · {slot['kind']}**")
                    st.write(f"{slot['starts_at'].replace('T',' ')}–{slot['ends_at'][11:16]} · {slot['location']}")
                    if slot['available']<1:st.caption('对应课时不足，或已被其他预约占用。')
                    key=f"student_request_{slot['id']}"
                    if st.button('预约这节课',key=f"student_book_{slot['id']}",disabled=slot['available']<1):
                        request=st.session_state.setdefault(key,uuid.uuid4().hex)
                        try:booking.book(token,slot['id'],slot['revision'],request)
                        except ValueError as error:st.error(str(error))
                        else:
                            st.session_state.pop(key,None);st.session_state['student_notice']='预约成功，已占用 1 节课时。';st.rerun()
        with mine:
            rows=booking.my_lessons(token)
            if rows:st.dataframe([{'课程ID':f"L{r['id']:03d}",'教练':r['coach_name'],'类型':r['kind'],'开始':r['starts_at'].replace('T',' '),'结束':r['ends_at'][11:16],'地点':r['location'],'状态':r['status']} for r in rows],hide_index=True,width='stretch')
            else:st.info('还没有课程。')
        with account:security(token)
    except staff_auth.AuthenticationError:
        clear();st.rerun()
    except (ValueError,RuntimeError) as error:st.error(str(error))


def security(token):
    with st.form('student_password_change'):
        old=st.text_input('原密码',type='password')
        new=st.text_input('新密码（15～128 个字符）',type='password')
        confirm=st.text_input('再次输入新密码',type='password')
        saved=st.form_submit_button('修改密码')
    if saved:
        if new!=confirm:st.error('两次输入的新密码不一致。');return
        try:auth.change_password(token,old,new)
        except ValueError as error:st.error(str(error))
        else:
            clear();st.success('密码已修改，请重新登录。');st.rerun()


def run():
    modern=False
    if store.available():
        with store.connect() as con:modern=credits.calendar_enabled(con)
    if modern:
        from student_calendar_app import run as calendar_run
        calendar_run()
    else:run_legacy()


if __name__=='__main__':run()

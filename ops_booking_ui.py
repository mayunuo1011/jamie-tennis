"""Staff publishing and student credential provisioning; no new finance permissions."""
from datetime import datetime,time,timedelta
import streamlit as st
import ops_auth as auth
import ops_booking as api
import ops_service as svc
import ops_credits as credits
import ops_store as store
import student_auth
import user_service as rules


def render(token):
    user=auth.current_user(token)
    with store.connect() as con:
        if not credits.booking_enabled(con):st.info('请先关闭网站并运行“升级预约版.bat”。');return
    with store.connect() as con:modern=credits.calendar_enabled(con)
    if modern:
        from ops_calendar_ui import render as calendar_render
        calendar_render(token);return
    st.subheader('开放预约')
    st.caption('只发布私教、陪打的未来时段；学员预约后自动进入本周课程。取消、改期仍在本周课程处理。')
    st.caption('关闭预约入口不会取消已生成的课程；修改空闲时段可先关闭旧入口，再重新发布。')
    teachers=svc.coaches(token)
    writable=[c for c in teachers if user['role']=='system_admin' or c['id']==user['coach_id']]
    if writable:
        with st.form('ops_publish_slot',clear_on_submit=True):
            cid=st.selectbox('授课教练',[c['id'] for c in writable],format_func=lambda i:next(c['name'] for c in writable if c['id']==i))
            kind=st.selectbox('课程类型',['私教','陪打'])
            day=st.date_input('日期',value=rules.today_london()+timedelta(days=1),min_value=rules.today_london())
            a,b=st.columns(2);begin=a.time_input('开始时间',time(9));end=b.time_input('结束时间',time(10))
            place=st.text_input('地点')
            save=st.form_submit_button('发布可预约时段',type='primary')
        if save:
            try:api.publish(token,kind,datetime.combine(day,begin).isoformat(timespec='minutes'),datetime.combine(day,end).isoformat(timespec='minutes'),place,cid)
            except ValueError as error:st.error(str(error))
            else:st.success('时段已发布。')
    rows=api.staff_slots(token)
    for slot in rows:
        with st.container(border=True):
            state='已关闭' if slot['closed'] else '已有课程' if slot['booked'] else '可预约'
            st.write(f"{slot['coach_name']} · {slot['kind']} · {slot['starts_at'].replace('T',' ')}–{slot['ends_at'][11:16]} · {slot['location']} · {state}")
            if not slot['closed'] and (user['role']=='system_admin' or slot['coach_id']==user['coach_id']):
                if st.button('关闭预约入口',key=f"ops_close_slot_{slot['id']}"):
                    try:api.close_slot(token,slot['id'],slot['revision'])
                    except ValueError as error:st.error(str(error))
                    else:st.rerun()
    if user['role'] in credits.ROLES:
        st.divider();st.subheader('学员登录账号')
        st.caption('先在学员档案建档、对接教练并录入课包，再创建登录账号。ID 本身不是密码。初始密码只在本次创建后显示，请单独发给对应学员。')
        people=svc.list_students(token)
        if not people:return
        selected=st.selectbox('选择学员',[s['id'] for s in people],format_func=lambda i:next(f"S{s['id']:03d} · {s['name']}" for s in people if s['id']==i),key='ops_student_login_sid')
        with store.connect() as con:
            auth.require(con,token,credits.ROLES)
            row=con.execute('SELECT active,must_change FROM student_accounts WHERE student_id=?',(selected,)).fetchone()
        if row:st.caption('账号已启用' if row['active'] else '账号已停用')
        if not row:
            if st.button('创建学员登录账号',key='ops_create_student_login'):
                try:result=student_auth.provision(token,selected)
                except ValueError as error:st.error(str(error))
                else:st.code(f"学员 ID：{result['username']}\n初始密码：{result['initial_password']}")
        else:
            with st.popover('重置学员密码'):
                st.write('确定重置？该学员所有已登录设备会退出，首次登录需修改新密码。')
                if st.button('确定重置',key='ops_reset_student_login'):
                    result=student_auth.provision(token,selected,True)
                    st.code(f"学员 ID：{result['username']}\n新初始密码：{result['initial_password']}")
            if st.button('停用学员登录' if row['active'] else '启用学员登录',key='ops_toggle_student_login'):
                student_auth.set_active(token,selected,not bool(row['active']));st.rerun()

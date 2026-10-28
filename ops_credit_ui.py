"""Jamie-only credit panel. No balance data is sent to coach pages."""
import uuid
import streamlit as st
import ops_auth as auth
import ops_service as students
import ops_credits as credits
import ops_store as store
import user_service as rules


def render(token):
    user=auth.current_user(token)
    if user['role'] not in credits.ROLES:raise auth.PermissionDenied('无课包管理权限。')
    with store.connect() as con:
        if not credits.enabled(con):
            st.info('先关闭网站，运行“升级课时管理.bat”，再重新启动。');return
    st.subheader('课包管理')
    st.caption('购买手工录入；私教与陪练、不同教练分别记账。排课不预扣，审核通过后扣1节。')
    people=students.list_students(token)
    teachers=students.coaches(token)
    if not people or not teachers:
        st.info('先在学员档案里添加学员，并确认教练账号已建立。');return
    names={p['id']:f"S{p['id']:03d} · {p['name']}" for p in people}
    coach_names={p['id']:p['name'] for p in teachers}
    sid=st.selectbox('学员',list(names),format_func=names.get,key='ops_credit_student')
    rows=credits.balances(token,sid)
    if rows:
        st.dataframe([{'教练':r['coach_name'],'类型':r['kind'],'剩余课时':r['remaining']} for r in rows],hide_index=True,width='stretch')
    else:st.info('该学员尚未录入课包。')
    purchase,history,correction,absences=st.tabs(['录入购买','购买与变动记录','录入纠错','缺课待处理'])
    with purchase:
        key=st.session_state.setdefault('ops_credit_request',uuid.uuid4().hex)
        with st.form('ops_credit_purchase',clear_on_submit=True):
            cid=st.selectbox('任课教练',list(coach_names),format_func=coach_names.get,key='ops_credit_coach')
            kind=st.selectbox('课程类型',['私教','陪打'],format_func=lambda x:'陪练' if x=='陪打' else x,key='ops_credit_kind')
            day=st.date_input('购买日期',value=rules.today_london(),key='ops_credit_day')
            quantity=st.number_input('购买节数',min_value=1,max_value=10000,value=10,step=1,key='ops_credit_quantity')
            saved=st.form_submit_button('确认录入课包',type='primary')
        if saved:
            credits.add_package(token,sid,cid,kind,day,int(quantity),key)
            st.session_state.pop('ops_credit_request',None)
            st.session_state['user_notice']='课包已录入，对应教练可以排课。'
            st.rerun()
    packages=credits.packages(token,sid)
    ledger=credits.history(token,sid)
    with history:
        st.write('购买记录')
        st.dataframe([{'课包':p['id'],'教练':p['coach_name'] or '归属待核对','类型':p['kind'],'购买日期':p['purchased_at'],'购买节数':p['purchased'],'剩余':p['remaining'],'状态':'已作废' if p['voided'] else '有效'} for p in packages],hide_index=True,width='stretch')
        st.write('课时变动历史')
        labels={'opening':'升级前余额','purchase':'购买','void_purchase':'作废未使用课包','lesson':'课程扣课','absence':'缺课扣课','return':'退还课时'}
        st.dataframe([{'记录':r['id'],'时间':r['created_at'],'教练':r['coach_name'],'类型':r['kind'],'变动':r['delta'],'原因':labels[r['action']],'课程':r['lesson_id'],'说明':r['reason'],'操作人':r['actor_name'] or '系统迁移'} for r in ledger],hide_index=True,width='stretch')
    with correction:
        st.caption('不删除历史：未使用课包可作废；已扣课程可退还1节，保留原课程完成状态。此处不处理现金退款。')
        eligible={p['id']:p for p in packages if not p['voided'] and not p['used'] and p['coach_id'] is not None and p['remaining']==p['purchased']}
        if eligible:
            with st.form('ops_credit_void'):
                pid=st.selectbox('作废未使用课包',list(eligible),format_func=lambda i:f"P{i} · {eligible[i]['coach_name']} · {eligible[i]['kind']} · {eligible[i]['purchased']}节")
                reason=st.text_input('作废原因')
                confirm=st.checkbox('确认这是录入错误的未使用课包')
                void=st.form_submit_button('作废这份课包')
            if void:
                if not confirm:st.error('请先勾选确认。')
                else:
                    credits.void_package(token,pid,reason);st.session_state['user_notice']='课包已作废，历史保留。';st.rerun()
        returned={r['lesson_id'] for r in ledger if r['action']=='return'}
        charges={r['lesson_id']:r for r in ledger if r['action'] in ('lesson','absence') and r['lesson_id'] not in returned}
        if charges:
            with st.form('ops_credit_return'):
                lid=st.selectbox('退还已扣课程',list(charges),format_func=lambda i:f"L{i} · {charges[i]['coach_name']} · {charges[i]['kind']}")
                reason=st.text_input('退还原因')
                confirm=st.checkbox('确认退还这节课的1节课时')
                returned=st.form_submit_button('退还1节')
            if returned:
                if not confirm:st.error('请先勾选确认。')
                else:
                    credits.return_credit(token,lid,reason);st.session_state['user_notice']='已退还1节，课程完成状态保留。';st.rerun()
    with absences:
        due=credits.pending_absences(token)
        st.caption('跨教练累计第二次起的临时缺课在这里处理。教练只记录，Jamie 确认扣课；余额不足保留未扣。')
        if not due:st.info('暂无待处理缺课。')
        for row in due:
            st.write(f"L{row['id']} · {row['student_name']} · {row['coach_name']} · {row['kind']} · {row['starts_at']}")
            if st.button('确认缺课扣1节',key=f"ops_credit_absence_{row['id']}"):
                result=credits.settle_absence(token,row['id'])
                if result=='unpaid':st.warning('余额不足，仍标记未扣课。补录后可再处理。')
                else:st.session_state['user_notice']='缺课已扣1节。';st.rerun()

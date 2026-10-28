"""Single-dialog interactions for feedback, review and class reminders."""
from datetime import datetime,time
import streamlit as st
import ops_auth as auth
import ops_service as svc
import ops_reviews as reviews
import ops_ui_service as api
import user_service as rules
import user_views as views
from ops_facade import WorkspaceService


def close_dialog():
    st.session_state.pop('ops_ui_dialog',None)
    views.reset_selection()


def open_dialog(kind,identifier):
    views.reset_selection()
    st.session_state['ops_ui_dialog']=(kind,identifier)


def selected_row(event):
    cells=getattr(event.selection,'cells',[])
    if cells:return cells[0][0]
    rows=getattr(event.selection,'rows',[])
    return rows[0] if rows else None


def success(message):
    st.session_state['user_notice']=message
    views.forget_versions()
    st.rerun()


def lesson_actions(token,row):
    user=auth.current_user(token)
    if user['role']!='system_admin' and user['coach_id']!=row['coach_id']:return
    if row['status'] in ('已取消','已改期','已完成') or row.get('review_state')=='approved':return
    with st.expander('取消 / 改期这节课'):
        mode=st.radio('处理方式',['取消','改期'],horizontal=True,key=f"ops_ui_action_{row['id']}")
        with st.form(f"ops_ui_action_form_{row['id']}"):
            if mode=='取消':
                reason=st.selectbox('取消理由',['学员取消','天气','教练原因','其他'])
                detail=st.text_input('补充说明（选填）')
                advance=st.checkbox('提前至少24小时取消（含24小时）')
                if WorkspaceService(token,row['coach_id']).automatic_absence_debit:
                    st.caption('学员不足24小时取消才计缺课：第一次不扣，跨教练累计第二次起自动扣对应课时1节，允许负余额。天气、教练原因不计缺课。')
            else:
                day=st.date_input('新日期（未确定可留空）',value=None)
                a,b=st.columns(2)
                begin=a.time_input('新开始时间',value=datetime.fromisoformat(row['starts_at']).time())
                end=b.time_input('新结束时间',value=datetime.fromisoformat(row['ends_at']).time())
                location=st.text_input('新地点',value=row['location'])
                reason=st.text_input('改期说明（选填）')
            saved=st.form_submit_button('确认取消' if mode=='取消' else '保存改期',type='primary')
        if saved:
            if mode=='取消':svc.cancel(token,row['id'],reason,advance,detail,row['revision'])
            else:svc.reschedule(token,row['id'],day,begin,end,location,reason,row['revision'])
            success('已取消课程。' if mode=='取消' else '改期已保存，对应周课表会同步更新。')


@st.dialog('课程详情',width='large',on_dismiss=close_dialog)
def lesson_dialog(token,lid):
    row=svc.lesson_detail(token,lid)
    st.subheader(f"{row['student_name']} · {row['kind']}")
    st.caption(f"S{row['student_id']:03d} · L{row['id']} · {row['starts_at'].replace('T',' ')}–{row['ends_at'][11:16]} · {row['location']}")
    try:
        user=auth.current_user(token)
        if row.get('manual_attendance') and not row.get('attendance_confirmed') and row['status'] not in ('已取消','已改期','已完成') and (user['role']=='system_admin' or user['coach_id']==row['coach_id']):
            st.caption('时间经过不会自动确认上课。可以手动确认，或课后直接保存反馈并打卡。')
            if st.button('确认已上课',key=f'ops_ui_attendance_{lid}'):
                svc.confirm_attendance(token,lid,row['revision']);success('已确认上课，尚未审核或扣课。')
        views.record_form(row,'ops_ui_lesson',WorkspaceService(token,row['coach_id']))
        user=auth.current_user(token)
        if row['checkin_done'] and row.get('review_state')!='approved' and (user['role']=='system_admin' or user['coach_id']==row['coach_id']):
            with st.expander('删除自己的课后反馈'):
                confirmed=st.checkbox('确认删除反馈，课程预约仍保留',key=f'ops_ui_feedback_clear_confirm_{lid}')
                if st.button('删除反馈',disabled=not confirmed,key=f'ops_ui_feedback_clear_{lid}'):
                    svc.clear_feedback(token,lid,row['revision']);success('反馈已删除，课程仍保留。')
        lesson_actions(token,row)
    except (ValueError,RuntimeError) as error:st.error(str(error))


@st.dialog('打卡审核',width='large',on_dismiss=close_dialog)
def review_dialog(token,lid):
    row=svc.lesson_detail(token,lid)
    # This endpoint is operations-only; looking at feedback never approves it.
    current=api.current_submission(token,lid)
    st.subheader(f"{row['student_name']} · {row['coach_name']} · {row['kind']}")
    st.caption(f"S{row['student_id']:03d} · L{lid} · {row['starts_at'].replace('T',' ')} · {row['location']}")
    if not current:
        st.info('还没有有效打卡，请任课教练先提交反馈。');return
    snapshot=f'ops_ui_review_snapshot_{lid}'
    opened=st.session_state.setdefault(snapshot,current['id'])
    if opened!=current['id']:
        st.warning('反馈已更新，请重新查看后再审核。')
        if st.button('重新载入反馈',key=f'ops_ui_review_reload_{lid}'):
            st.session_state[snapshot]=current['id'];st.rerun()
        return
    st.write('课后总结');st.write(current['note'])
    st.write('下次目标');st.write(current['next_goal'])
    if current['state']=='pending':
        if st.button('通过审核并扣1节',type='primary',key=f"ops_ui_approve_{lid}_{current['id']}"):
            try:
                reviews.decide(token,current['id'],'approved');success('已审核完成，已扣1节。')
            except (ValueError,RuntimeError) as error:st.error(str(error))
        with st.expander('退回给教练'):
            with st.form(f'ops_ui_return_{lid}'):
                reason=st.text_area('退回原因')
                returned=st.form_submit_button('退回修改')
            if returned:
                try:reviews.decide(token,current['id'],'returned',reason);success('反馈已退回。')
                except (ValueError,RuntimeError) as error:st.error(str(error))
    elif current['state']=='approved':
        st.success('已完成审核')
        with st.popover('撤销完成'):
            st.warning('确定撤销完成吗？将恢复待审核，并退回本次扣掉的1节；已经退还的不会重复加回。')
            if st.button('确定撤销完成',key=f"ops_ui_undo_{lid}_{current['id']}"):
                try:api.undo_completion(token,lid,current['id']);close_dialog();success('已恢复待审核，本次扣课已退回或此前已退还。')
                except (ValueError,RuntimeError) as error:st.error(str(error))
    else:st.info('反馈已退回，请等待教练重新提交。')


def group_form(token,row=None):
    user=auth.current_user(token)
    if user['role'] not in ('operator','system_admin'):return
    if row and user['role']!='system_admin' and row['coach_id']!=user['coach_id']:return
    revision=st.session_state.setdefault(f"ops_ui_group_revision_{row['id']}",row['revision']) if row else None
    people=svc.list_students(token)
    names={p['id']:f"S{p['id']:03d} · {p['name']}" for p in people}
    with st.form(f"ops_ui_group_form_{row['id'] if row else 'new'}"):
        name=st.text_input('团课班名',value=row['name'] if row else '',placeholder='例如：新手小白5班')
        day=st.date_input('日期',value=datetime.fromisoformat(row['starts_at']).date() if row else rules.today_london())
        a,b=st.columns(2)
        begin=a.time_input('开始时间',value=datetime.fromisoformat(row['starts_at']).time() if row else time(10))
        end=b.time_input('结束时间',value=datetime.fromisoformat(row['ends_at']).time() if row else time(11))
        location=st.text_input('地点',value=row['location'] if row else 'Meadows')
        members=st.multiselect('班级学员（选填，仅用于显示班级）',list(names),default=row['members'] if row else [],format_func=names.get)
        saved=st.form_submit_button('保存团课提醒',type='primary')
    if saved:
        try:
            api.save_group(token,name,datetime.combine(day,begin).isoformat(timespec='minutes'),datetime.combine(day,end).isoformat(timespec='minutes'),location,members,
                           row['id'] if row else None,revision)
            success('团课提醒已保存，不涉及个人扣课。')
        except (ValueError,RuntimeError) as error:st.error(str(error))


@st.dialog('团课安排',width='large',on_dismiss=close_dialog)
def group_dialog(token,gid):
    row=api.group_detail(token,gid)
    st.subheader(row['name']);st.caption(f"{row['coach_name']} · {row['starts_at'].replace('T',' ')} · {row['location']}")
    st.caption('仅作课表提醒，不打卡审核，不扣个人课时。')
    group_form(token,row)
    user=auth.current_user(token)
    if user['role']=='system_admin' or (user['role']=='operator' and row['coach_id']==user['coach_id']):
        with st.popover('移除这次团课提醒'):
            st.warning('确定从课表移除吗？不影响任何个人课时。')
            if st.button('确定移除团课',key=f'ops_ui_group_remove_{gid}'):
                api.archive_group(token,gid,row['revision']);close_dialog();success('团课提醒已移除。')

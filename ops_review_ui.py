"""Operations dashboard; viewing/reviewing never grants course-edit rights."""
from datetime import timedelta
import streamlit as st
import ops_auth as auth
import ops_reviews as reviews
import user_views as workspace
from ops_facade import WorkspaceService


def open_inbox():
    st.session_state['ops_navigation']='课程管理'
    st.session_state['review_management_view']='反馈审核'


def notice(token):
    user=auth.current_user(token)
    stats=reviews.inbox_stats(token)
    if user['role'] in ('operator','system_admin'):
        if stats['pending']:
            st.info(f"待审核 {stats['pending']} 节 · 其中 {stats['unread']} 条新反馈尚未查看")
            st.button('打开待审核反馈',key='review_open_inbox',on_click=open_inbox)
        else:st.caption('暂无待审核反馈。')
    elif stats['returned']:
        st.warning(f"有 {stats['returned']} 条反馈被退回，请从课程或学员训练记录中查看原因、修改后重新提交。")


def render_management(token):
    user=auth.current_user(token)
    if user['role'] not in ('operator','system_admin'):raise auth.PermissionDenied('该界面仅供运营审核。')
    st.subheader('运营课程管理')
    start=workspace.week_controls()
    st.caption(f'所选周：{start} — {start+timedelta(days=6)}')
    view=st.radio('管理视图',['全体课程总览','反馈审核','我自己的排课'],horizontal=True,key='review_management_view')
    if view=='全体课程总览':
        records=reviews.weekly_overview(token,start)
        if records:st.dataframe(records,hide_index=True,width='stretch',key='review_week_overview')
        else:st.info('这周暂无课程。')
        st.caption('全体课程为只读。确认完成、打卡和扣课分别显示；请在反馈审核中确认，在课包管理中登记购买。')
    elif view=='反馈审核':render_review_panel(token,start)
    else:
        if user['coach_id'] is None:
            st.info('当前账号没有教练身份。需要管理更正时，请在本周课程中选择具体教练操作。')
        else:
            st.caption('下面管理的是你自己的本周预约，与教练端使用相同规则。')
            workspace.management_page(WorkspaceService(token,user['coach_id']))


def render_review_panel(token,start):
    state=st.selectbox('审核列表',['pending','approved','returned','all'],
        format_func=lambda s:{**reviews.STATES,'all':'全部提交（含历史版本）'}[s],key='review_state_filter')
    only_week=st.checkbox('只看所选周的课程',value=False,key='review_only_week')
    records=reviews.list_submissions(token,None if state=='all' else state,history=state=='all')
    if only_week:
        records=[r for r in records if start.isoformat()<=r['starts_at'][:10]<(start+timedelta(days=7)).isoformat()]
    if not records:
        st.session_state.pop('review_opened',None)
        st.info('暂无符合条件的反馈。');return
    mapping={r['id']:r for r in records}
    sid=st.selectbox('选择反馈',list(mapping),format_func=lambda i:f"{mapping[i]['student_name']} · {mapping[i]['coach_name']} · {mapping[i]['starts_at'].replace('T',' ')} · {reviews.STATES[mapping[i]['state']]}",key=f'review_submission_{state}_{only_week}')
    # Selecting a row is distinct from approving it.
    if st.button('查看这份反馈',key=f'review_read_{sid}'):
        reviews.open_submission(token,sid)
        st.session_state['review_opened']=sid
        st.rerun()
    if st.session_state.get('review_opened')!=sid:
        st.caption('点击查看后阅读总结，再决定是否审核。');return
    row=reviews.open_submission(token,sid)
    st.markdown(f"**{row['student_name']} · {row['coach_name']} · {row['kind']}**")
    st.caption(f"{row['starts_at'].replace('T',' ')}–{row['ends_at'][11:16]} · {row['location']} · 提交于 {row['submitted_at']}")
    if row['source']=='migration':st.caption('历史有效反馈，升级时加入待审核；尚未自动确认。')
    st.write('课后总结');st.write(row['note'])
    st.write('下次目标');st.write(row['next_goal'])
    st.write('审核状态：'+reviews.STATES[row['state']])
    if row['reviewer_name']:st.caption(f"审核人：{row['reviewer_name']} · {row['reviewed_at']}")
    if row['state']=='approved' and row['reviewed_at'] and row['submitted_at']>row['reviewed_at']:
        st.caption('反馈在审核完成后有更新；完成与扣课沿用原审核，原反馈保留在历史提交中。')
    if row['review_comment']:st.write('审核说明：'+row['review_comment'])
    import ops_store
    from ops_credits import enabled,lesson_credit_state
    with ops_store.connect() as con:
        credit_enabled=enabled(con)
        credit_state=lesson_credit_state(con,row['lesson_id'])
    st.caption(('课时处理：'+credit_state+'。通过审核会扣对应教练、类型的1节。') if credit_enabled else '课时处理：尚未开启课时管理，审核不会扣课。')
    if not row['is_current'] or row['state'] not in ('pending','approved'):
        st.caption('这是历史版本或已处理反馈，不能再次确认。');return
    with st.form(f'review_decision_{sid}'):
        comment=st.text_area('审核说明 / 退回原因',key=f'review_comment_{sid}',placeholder='退回时必填，直接说明需要教练补充什么')
        approve=st.form_submit_button('确认审核完成',type='primary',disabled=row['state']!='pending')
        returned=st.form_submit_button('退回修改',disabled=credit_enabled and row['state']=='approved')
    if approve or returned:
        try:result=reviews.decide(token,sid,'approved' if approve else 'returned',comment)
        except (ValueError,RuntimeError) as error:st.error(str(error))
        else:
            workspace.forget_versions()
            st.session_state['user_notice']=('已审核完成，已扣1节。' if result['credit_action']=='charged' else '已审核完成，未扣课。') if approve else '已退回，教练修改后会重新进入待审核。'
            st.session_state.pop('review_opened',None)
            st.rerun()

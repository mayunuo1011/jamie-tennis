from datetime import date, datetime, time, timedelta
from html import escape

import pandas as pd
import streamlit as st

import user_service as legacy_svc
import lesson_export as exports


def forget_versions():
    for key in list(st.session_state):
        if key.startswith('ops_version_'):st.session_state.pop(key,None)


def version_args(service,row,prefix):
    return {'expected_revision':service.version(row,prefix)} if getattr(service,'guarded',False) else {}


def reset_selection():
    forget_versions()
    st.session_state.pop('ops_ui_dialog',None)
    for key in list(st.session_state):
        if key.startswith(('ops_ui_review_snapshot_','ops_ui_group_revision_')):st.session_state.pop(key,None)
    st.session_state['dialog_active']=False
    st.session_state['active_dialog']=None
    st.session_state['table_epoch']=st.session_state.get('table_epoch',0)+1


def flash(message):
    st.session_state['user_notice']=message
    reset_selection()
    st.rerun()


def highlight(row):
    return ['background-color:#fff0ed;color:#8b3941' if row.get('缺课提醒','') else '' for _ in row]


def lesson_rows(records):
    return [{
        '称呼':l['student_name'],
        **({'教练':l.get('coach_name') or '待确认'} if 'coach_name' in l else {}),
        '日期':l['starts_at'][:10],
        '时段':l['starts_at'][11:16]+'–'+l['ends_at'][11:16],
        '地点':l['location'],
        '上课状态':legacy_svc.attendance(l),
        '打卡状态':'已打卡' if l['checkin_done'] else '未打卡',
        **({'反馈审核':{'pending':'待审核','approved':'已审核完成','returned':'已退回修改','withdrawn':'已撤回'}.get(l.get('review_state'),'未有效提交')} if 'review_state' in l else {}),
        '缺课提醒':f"累计 {l['absence_count']} 次" if l['absence_count']>=2 else '',
    } for l in records]


def record_form(l, prefix, service=None):
    svc=service or legacy_svc
    if getattr(svc,'guarded',False) and (not svc.feedback_access(l) or l['status'] in ('已取消','已改期') or (l.get('review_state')=='approved' and not getattr(svc,'approved_feedback_editable',False))):
        st.write(l.get('note') or '暂无课后总结')
        st.write('下次目标：'+(l.get('next_goal') or '暂无'))
        st.caption(('已审核，其他教练的反馈仅可查看。' if getattr(svc,'approved_feedback_editable',False) else '反馈已审核锁定，需 Jamie 退回后才能修改。') if l.get('review_state')=='approved' else '此处为只读反馈。')
        return
    version=version_args(svc,l,f'{prefix}_{l["id"]}')
    with st.form(f'{prefix}_record_{l["id"]}'):
        if getattr(svc,'guarded',False):
            st.caption('课程已完成：修改反馈保留完成状态，不会再次扣课。' if l.get('review_state')=='approved' and getattr(svc,'approved_feedback_editable',False) else '保存打卡会提交最新反馈并提醒 Jamie 审核；提交不表示审核通过或已经扣课。')
        st.caption('两个框都必填，各至少 11 个中文字符；英文、数字、空格和标点不计入。')
        note=st.text_area('课后总结 / 训练记录',value=l['note'],height=140,placeholder='今天练了什么、表现如何……')
        goal=st.text_area('下次目标',value=l['next_goal'],height=75)
        saved=st.form_submit_button('保存修改' if l['checkin_done'] else '保存并打卡',type='primary')
    if saved:
        try:
            svc.check_in(l['id'],note,goal,**version)
        except Exception as error:
            st.error(str(error))
            if error.__class__.__name__=="StaleRecord":st.button("重新载入记录",on_click=forget_versions,key="reload_"+str(hash(str(error))))
        else:
            forget_versions()
            st.session_state['dialog_notice']='记录已保存。'
            st.rerun()


@st.dialog('课程详情',width='large',on_dismiss=reset_selection)
def lesson_dialog(lid, service=None):
    svc=service or legacy_svc
    l=svc.lesson(lid)
    if st.session_state.get('dialog_notice'):
        st.success(st.session_state.pop('dialog_notice'))
    st.subheader(f"{l['student_name']} · {l['kind']}")
    st.caption(f"{l['starts_at'][:10]}　{l['starts_at'][11:16]}–{l['ends_at'][11:16]}　{l['location']}")
    st.write(f"上课状态：**{svc.attendance(l)}**　　打卡状态：**{'已打卡' if l['checkin_done'] else '未打卡'}**")
    if 'review_state' in l:
        st.caption('反馈审核：'+{'pending':'待审核','approved':'已审核完成','returned':'已退回修改','withdrawn':'已撤回'}.get(l.get('review_state'),'未有效提交'))
        if l.get('review_comment'):st.write('Jamie 的审核说明：'+l['review_comment'])
    if l['absence_count']>=2:
        st.warning(f"该学员累计缺课 {l['absence_count']} 次，请留意。")
    if l['reason']:
        st.caption('取消理由：'+l['reason'])
    if l['moved_to']:
        st.caption('已改到：'+l['moved_to'].replace('T',' '))
    elif l['reschedule_pending']:
        st.caption('新日期待定，可在课程管理中补充。')
    record_form(l,'lesson_dialog',svc)
    if getattr(svc,'guarded',False) and svc.feedback_access(l) and l['checkin_done'] and l.get('review_state')!='approved':
        with st.expander('删除这节课的反馈'):
            st.caption('删除后恢复为未打卡；课程预约保留，并记录此次操作。')
            clear_version=version_args(svc,l,f'clear_feedback_{lid}')
            confirmed=st.checkbox('确认删除自己的课后反馈',key=f'clear_feedback_confirm_{lid}')
            if st.button('删除反馈',disabled=not confirmed,key=f'clear_feedback_submit_{lid}'):
                try:svc.clear_feedback(lid,clear_version['expected_revision'])
                except Exception as error:st.error(str(error))
                else:flash('反馈已删除，课程保留。')
    if st.button('关闭',key='close_lesson_dialog'):
        reset_selection()
        st.rerun()


def student_form(p=None, service=None):
    svc=service or legacy_svc
    p=p or {}
    sid=p.get('id')
    private=getattr(svc,'teaching_access',lambda p:True)(p)
    version=version_args(svc,p,f'student_{sid}') if sid else {}
    with st.form(f'user_student_{sid or "new"}',clear_on_submit=sid is None):
        name=st.text_input('姓名 / 称呼 *',value=p.get('name',''))
        a,b=st.columns(2)
        with a:
            contact=st.text_input('联系方式（选填）',value=p.get('contact',''))
            level=st.text_input('网球水平（教练私有）',value=p.get('level','')) if private else ''
        with b:
            source=st.text_input('通过什么途径找到你',value=p.get('source',''))
            joined=st.date_input('加入日期',value=date.fromisoformat(p['joined_at']) if p.get('joined_at') else None if sid else svc.today_london(),format='YYYY-MM-DD')
        pain=st.text_area('技术痛点',value=p.get('pain_points','')) if private else ''
        goals=st.text_area('教学目标',value=p.get('goals','')) if private else ''
        remarks=p.get('remarks','')
        if remarks == '由课表导入；学员信息和购买课时待补录。':
            remarks='由课表导入，基本信息待补充。'
        notes=st.text_area('备注（教练私有）',value=remarks) if private else ''
        if not private:st.caption('只显示公共资料；其他教练的教学档案不可见。')
        save=st.form_submit_button('保存档案' if sid else '新增学员',type='primary')
    if save:
        try:
            svc.save_student(name,contact,source,level,pain,goals,notes,joined.isoformat() if joined else None,sid,**version)
        except Exception as error:
            st.error(str(error))
            if error.__class__.__name__=="StaleRecord":st.button("重新载入记录",on_click=forget_versions,key="reload_"+str(hash(str(error))))
        else:
            if sid:
                forget_versions()
                st.session_state['dialog_notice']='档案已保存。'
                st.rerun()
            else:
                flash('学员已添加。')


@st.dialog('学员档案',width='large',on_dismiss=reset_selection)
def profile_dialog(sid, service=None):
    svc=service or legacy_svc
    p=svc.student(sid)
    if st.session_state.get('dialog_notice'):
        st.success(st.session_state.pop('dialog_notice'))
    if not p:
        st.error('学员不存在。')
        return
    st.subheader(f"{p['name']}'s Profile")
    st.caption(f"S{sid:03d}　加入日期：{p['joined_at'] or '待补充'}")
    if st.button('★ 取消星标' if p['starred'] else '☆ 星标置顶',key=f'profile_star_{sid}'):
        svc.set_star(sid,not p['starred'])
        st.rerun()
    if p['absence_count']>=2:
        st.warning(f"累计缺课 {p['absence_count']} 次，请留意。")
    history,basic=st.tabs(['训练记录','基本信息'])
    with history:
        records=list(reversed(svc.lessons(sid=sid)))
        if not records:
            st.info('还没有课程记录。')
        for l in records:
            title=f"{l['starts_at'][:10]}　{l['starts_at'][11:16]}–{l['ends_at'][11:16]}　{l['location']}　{'已打卡' if l['checkin_done'] else '未打卡'}"
            with st.expander(title):
                st.caption('上课状态：'+svc.attendance(l))
                if l['note']:
                    st.write(l['note'])
                if l['next_goal']:
                    st.caption('下次目标：'+l['next_goal'])
                record_form(l,f'profile_{sid}',svc)
    with basic:
        if getattr(svc,'profile_read_only',False):
            for label,key in [('称呼','name'),('联系方式','contact'),('学员来源','source'),('加入日期','joined_at')]:
                st.write(f"{label}：{p.get(key) or '未填写'}")
        else:student_form(p,svc)
    if st.button('关闭',key='close_profile_dialog'):
        reset_selection()
        st.rerun()


def select_table(records, key, profile=False):
    if not records:
        st.info('没有符合条件的记录。')
        return
    if profile:
        data=[{'星标':'★' if p['starred'] else '', '称呼':p['name'],'加入时间':p['joined_at'] or '待补充','水平':(p.get('level') or '待补充') if 'level' in p else '教练私有','缺课提醒':f"累计 {p['absence_count']} 次" if p['absence_count']>=2 else ''} for p in records]
    else:
        data=lesson_rows(records)
    frame=pd.DataFrame(data)
    styled=frame.style.apply(highlight,axis=1)
    epoch=st.session_state.get('table_epoch',0)
    event=st.dataframe(styled,hide_index=True,width='stretch',on_select='rerun',selection_mode='single-row',key=f'{key}_{epoch}')
    st.caption('点击一行，打开'+('学员档案。' if profile else '课程详情与打卡。'))
    if event.selection.rows and not st.session_state.get('active_dialog'):
        index=event.selection.rows[0]
        if index<len(records):
            st.session_state['dialog_active']=True
            st.session_state['active_dialog']=('profile' if profile else 'lesson',records[index]['id'])


def week_controls():
    offset=st.session_state.get('week_offset',0)
    start=legacy_svc.week_start()+timedelta(weeks=offset)
    a,b,c=st.columns([1,1,1])
    with a:
        if st.button('← 上一周',key='previous_week'):
            st.session_state['week_offset']=offset-1
            reset_selection()
            st.rerun()
    with b:
        if st.button('回到本周',key='current_week'):
            st.session_state['week_offset']=0
            reset_selection()
            st.rerun()
    with c:
        if st.button('下一周 →',key='next_week'):
            st.session_state['week_offset']=offset+1
            reset_selection()
            st.rerun()
    return start


def weekly_page(service=None):
    svc=service or legacy_svc
    st.subheader('本周课程')
    start=week_controls()
    st.write(f"**{start:%Y-%m-%d} — {start+timedelta(days=6):%Y-%m-%d}**")
    records=svc.lessons(start)
    upcoming=[l for l in records if svc.attendance(l)=='没上']
    pending=[l for l in records if svc.attendance(l)=='上了' and not l['checkin_done']]
    a,b=st.columns(2)
    a.metric('待上课',f'{len(upcoming)} 节')
    b.metric('待打卡',f'{len(pending)} 节')
    selection=st.selectbox('筛选课程',['全部','待上课','待打卡','已打卡','已取消','已改期'],key='week_filter')
    filtered=[l for l in records if selection=='全部' or (selection=='待上课' and svc.attendance(l)=='没上') or (selection=='待打卡' and svc.attendance(l)=='上了' and not l['checkin_done']) or (selection=='已打卡' and l['checkin_done']) or (selection=='已取消' and l['status']=='已取消') or (selection=='已改期' and l['status']=='已改期')]
    select_table(filtered,f'week_table_{start}_{selection}')
    st.caption('正常课程按排定结束时间显示“上了”；取消、改期优先显示。')
    export_panel(records, start)
    if not getattr(svc,'can_manage_courses',True):
        st.caption('当前教练的课程为只读；请切换到自己，管理自己的排课。')
        return
    with st.expander('手动添加课程'):
        people=svc.students()
        if not people:
            st.info('先到学员档案添加学员，或上传课表。')
        else:
            labels={p['id']:f"{p['name']} · S{p['id']:03d}" for p in people}
            with st.form('user_add_lesson',clear_on_submit=True):
                sid=st.selectbox('学员',list(labels),format_func=labels.get)
                kind=st.selectbox('课程类型',['私教','陪打'])
                day=st.date_input('上课日期',value=svc.today_london(),format='YYYY-MM-DD')
                a,b=st.columns(2)
                begin=a.time_input('开始时间',value=time(14))
                end=b.time_input('结束时间',value=time(15))
                location=st.text_input('地点',value='Meadows')
                saved=st.form_submit_button('添加课程',type='primary')
            if saved:
                try:
                    svc.create_lesson(sid,kind,datetime.combine(day,begin).isoformat(timespec='minutes'),datetime.combine(day,end).isoformat(timespec='minutes'),location)
                except Exception as error:
                    st.error(str(error))
                    if error.__class__.__name__=="StaleRecord":st.button("重新载入记录",on_click=forget_versions,key="reload_"+str(hash(str(error))))
                else:
                    flash('课程已添加。')


def export_panel(records, start):
    with st.expander('导出课后记录'):
        eligible=exports.eligible_lessons(records,start)
        st.caption('导出当前所选周内已上课、已打卡且两个记录框均符合字数要求的课程。')
        if not eligible:
            st.info('暂无可导出的记录，请先完成课后打卡。')
            return
        mapping={l['id']:l for l in eligible}
        selected=st.multiselect('选择学员及课程',list(mapping),
            format_func=lambda lid:f"{mapping[lid]['student_name']} · {exports.lesson_label(mapping[lid])}",
            placeholder='可多选，每项是一节课',key=f'export_lessons_{start}')
        chosen=exports.select_records(records,selected,start)
        if not chosen:
            return
        st.caption('每位学员单独一段文字，点击文字框右上角的复制图标，即可粘贴到微信。')
        for group in exports.grouped_records(chosen):
            st.markdown(f"**{escape(group[0]['student_name'])}**")
            st.code(exports.student_text(group),language=None,wrap_lines=True)
        a,b=st.columns(2)
        stem=f'训练记录_{start}'
        a.download_button('下载 TXT',data=exports.export_txt(chosen),file_name=stem+'.txt',
            mime='text/plain; charset=utf-8',key=f'export_txt_{start}',on_click='ignore')
        b.download_button('下载 Word',data=exports.export_docx(chosen),file_name=stem+'.docx',
            mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            key=f'export_word_{start}',on_click='ignore')
        st.caption('下载文件会合并你选中的学员记录。')


def students_page(service=None):
    svc=service or legacy_svc
    st.subheader('学员档案')
    st.write(f'目前共有 **{len(svc.students())} 位**学员')
    with st.expander('＋ 新增学员'):
        student_form(service=svc)
    search=st.text_input('搜索学员',placeholder='称呼或 Student ID',key='user_student_search')
    select_table(svc.students(search),f'student_table_{search}',profile=True)
    st.caption('星标优先，其余按姓名首字母排列；中文常用字按拼音首字母。')


def management_page(service=None,start=None,show_heading=True):
    svc=service or legacy_svc
    if show_heading:st.subheader('课程管理')
    if not getattr(svc,'can_manage_courses',True):
        st.info('其他教练的排课仅可查看，取消、改期、添加和移除由任课教练本人操作。')
        select_table(svc.lessons(svc.week_start()),'readonly_course_management')
        return
    monday=start or svc.week_start()
    st.caption(f'本周：{monday} — {monday+timedelta(days=6)}')
    records=svc.lessons(monday)
    active=[l for l in records if l['status'] not in ('已取消','已改期') and (start is None or l.get('review_state')!='approved')]
    pending=[l for l in svc.lessons() if l['status']=='已改期' and l['reschedule_pending']]
    cancel_tab,move_tab,pending_tab=st.tabs(['取消课程','改期',f'待定改期 · {len(pending)}'])
    with cancel_tab:
        if not active:
            st.info('本周没有可取消的课程。')
        else:
            mapping={l['id']:l for l in active}
            lid=st.selectbox('选择要取消的课程',list(mapping),format_func=lambda i:f"{mapping[i]['student_name']} · {mapping[i]['starts_at'].replace('T',' ')} · {mapping[i]['location']}",key='user_cancel_selector')
            count=mapping[lid]['absence_count']
            if count>=2:
                st.warning(f'该学员累计缺课 {count} 次，请留意。')
            else:
                st.caption(f'该学员累计缺课 {count} 次。')
            version=version_args(svc,mapping[lid],f'cancel_{lid}')
            with st.form(f'user_cancel_{lid}'):
                reason=st.selectbox('取消理由',['学员取消','天气','教练原因','其他'])
                detail=st.text_input('补充说明（选填）')
                advance=st.checkbox('提前至少 24 小时取消（含恰好 24 小时）')
                st.caption('学员取消且不足 24 小时计缺课；其他原因不计学员缺课。')
                if getattr(svc,'automatic_absence_debit',False):st.caption('跨教练累计：第一次仅记缺课；第二次起自动扣对应课时1节，允许负余额，不需要审核。')
                saved=st.form_submit_button('确认取消',type='primary')
            if saved:
                try:
                    svc.cancel(lid,reason,advance,detail,**version)
                except Exception as error:
                    st.error(str(error))
                    if error.__class__.__name__=="StaleRecord":st.button("重新载入记录",on_click=forget_versions,key="reload_"+str(hash(str(error))))
                else:
                    flash('课程已取消，缺课提醒已更新。')
    with move_tab:
        reschedule_form(active,'week',svc)
    with pending_tab:
        st.caption('包含之前各周尚未确定新日期的课程。')
        reschedule_form(pending,'pending',svc)
    if getattr(svc,'guarded',False):
        with st.expander('移除误添加的课程'):
            removable=[l for l in active if not l['checkin_done'] and l['status']=='待上课' and not l['original_lesson_id']]
            if not removable:st.caption('没有可移除的普通预约。')
            else:
                mapping={l['id']:l for l in removable}
                lid=st.selectbox('选择误添加课程',list(mapping),format_func=lambda i:f"{mapping[i]['student_name']} · {mapping[i]['starts_at'].replace('T',' ')}",key='ops_archive_selector')
                version=version_args(svc,mapping[lid],f'archive_{lid}')
                confirmed=st.checkbox('确认这是误添加的课程',key='ops_archive_confirm')
                if st.button('移除课程',disabled=not confirmed,key='ops_archive_submit'):
                    try:svc.archive_lesson(lid,version['expected_revision'])
                    except Exception as error:st.error(str(error))
                    else:flash('课程已移除，保留操作记录。')


def reschedule_form(records,prefix,service=None):
    svc=service or legacy_svc
    if not records:
        st.info('没有需要改期的课程。')
        return
    mapping={l['id']:l for l in records}
    lid=st.selectbox('选择改期课程',list(mapping),format_func=lambda i:f"{mapping[i]['student_name']} · 原定 {mapping[i]['starts_at'].replace('T',' ')}",key=f'user_move_{prefix}')
    l=mapping[lid]
    version=version_args(svc,l,f'move_{prefix}_{lid}')
    with st.form(f'user_move_form_{prefix}_{lid}'):
        day=st.date_input('改到哪一天（未确定可留空）',value=None,format='YYYY-MM-DD')
        a,b=st.columns(2)
        begin=a.time_input('新开始时间',value=time.fromisoformat(l['proposed_start']) if l['proposed_start'] else datetime.fromisoformat(l['starts_at']).time())
        end=b.time_input('新结束时间',value=time.fromisoformat(l['proposed_end']) if l['proposed_end'] else datetime.fromisoformat(l['ends_at']).time())
        place=st.text_input('新地点',value=l['proposed_location'] or l['location'])
        reason=st.text_input('改期说明（选填）',value=l['change_reason'])
        saved=st.form_submit_button('保存改期',type='primary')
    if saved:
        try:
            svc.reschedule(lid,day,begin,end,place,reason,**version)
        except Exception as error:
            st.error(str(error))
            if error.__class__.__name__=="StaleRecord":st.button("重新载入记录",on_click=forget_versions,key="reload_"+str(hash(str(error))))
        else:
            flash('已保存为待定改期。' if day is None else f'已改到 {day}，对应周课表已更新。')


def upcoming_cards(service=None):
    svc=service or legacy_svc
    st.subheader('接下来的课程')
    now=svc.now_london().replace(tzinfo=None)
    records=[l for l in svc.lessons() if l['status'] not in ('已取消','已改期') and datetime.fromisoformat(l['ends_at'])>now][:3]
    if not records:
        st.caption('暂时没有接下来的课程。')
    for l in records:
        with st.container(border=True):
            st.markdown(f'<div class="upcoming-name">{escape(l["student_name"])} · {escape(l["kind"])}</div>',unsafe_allow_html=True)
            st.markdown(f'<div class="upcoming-time">{escape(l["starts_at"].replace("T"," "))}–{escape(l["ends_at"][11:16])}</div>',unsafe_allow_html=True)
            cls='meadows' if l['location'].casefold()=='meadows' else 'grange' if l['location'].casefold()=='grange club' else ''
            st.markdown(f'<span class="court {cls}">{escape(l["location"])}</span>',unsafe_allow_html=True)


def _persist_note(key,day,service=None):
    svc=service or legacy_svc
    try:
        svc.save_daily_note(day,st.session_state[key])
        st.session_state['daily_note_error']=None
    except Exception as error:
        st.session_state['daily_note_error']=str(error)


def _toggle_notes():
    st.session_state['notes_minimized']=not st.session_state.get('notes_minimized',False)


def reload_note(day):
    st.session_state.pop(f'ops_note_version_{day}',None)
    st.session_state.pop(f'daily_note_body_{day}',None)
    st.session_state['daily_note_error']=None


@st.fragment(run_every=30)
def sticky_notes(service=None):
    svc=service or legacy_svc
    with st.container(key='notes_panel'):
        minimized=st.session_state.get('notes_minimized',False)
        a,b=st.columns([2,1])
        a.markdown('**随手记**')
        b.button('展开' if minimized else '收起',key='toggle_daily_notes',on_click=_toggle_notes)
        if minimized:
            return
        today=svc.today_london()
        selected=st.date_input('查看日期',value=today,max_value=today,format='YYYY-MM-DD',key=f'notes_day_{today}')
        day=selected.isoformat()
        saved=svc.daily_note(day)
        key=f'daily_note_body_{day}'
        st.text_area('随手记内容',value=saved['body'],height=150,key=key,on_change=_persist_note,args=(key,day,svc),label_visibility='collapsed',placeholder='今天要记住的事情……')
        if st.session_state.get('daily_note_error'):
            st.error('保存失败：'+st.session_state['daily_note_error'])
            st.button('重新载入随手记',on_click=reload_note,args=(day,),key='ops_reload_note')
        else:
            st.caption('编辑后点击空白处自动保存。英国零点开启新一天，历史保留。')


def dispatch_dialog(service=None):
    svc=service or legacy_svc
    active=st.session_state.get('active_dialog')
    if active:
        kind,identifier=active
        if kind=='profile':
            profile_dialog(identifier,svc)
        else:
            lesson_dialog(identifier,svc)

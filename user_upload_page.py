import hashlib
from datetime import date
from user_service import today_london

import streamlit as st

from user_service import students as get_students
from user_import_service import hourly_slots, preview_sheet_import
from user_service import import_bookings as import_sheet_lessons
from slot_reader import parse_xiaoluo_xlsx, clean_name


def render_upload_page(service=None):
    get_people=service.students if service else get_students
    preview=service.preview_import if service else preview_sheet_import
    save=service.import_bookings if service else import_sheet_lessons
    st.subheader('上传小罗课表')
    if service:
        if not service.can_import_sheet():
            st.info('当前 Excel 导入器供 Luo 导入自己的小罗区域。其他教练的课表不能代为新增；其他格式暂用自己的手动排课。')
            return
        st.caption('固定读取小罗区域，课程归属为 Luo。')
    notice = st.session_state.pop('slot_import_notice', None)
    if notice:
        st.success(notice)
    upload = st.file_uploader('选择 Excel 文件', type=['xlsx'], key='slot_upload')
    if not upload:
        st.caption('粉色 → Meadows　蓝色 → Grange Club。新姓名自动建档。')
        return
    content = upload.getvalue()
    token = hashlib.sha256(content).hexdigest()[:16]
    with st.expander('更多选项'):
        year = int(st.number_input('课表年份', min_value=1900, max_value=2200, value=today_london().year, step=1, key=f'slot_year_{token}'))
        kind = st.selectbox('课程类型', ['私教', '陪打'], key=f'slot_kind_{token}')
    try:
        parsed = parse_xiaoluo_xlsx(content, year)
        slots = hourly_slots(parsed)
    except Exception as error:
        st.error(f'未导入：{error}')
        return
    st.write(f"**{parsed['week_start']} — {parsed['week_end']}**　·　{len(slots)} 节{kind}")
    st.caption('地点已按颜色识别；新姓名自动建档。连续两小时保留两节。')
    groups = [r for r in parsed['records'] if r['group_label']]
    if groups:
        st.caption('班级标记已跳过：' + '、'.join(r['name'] for r in groups))
    if not slots:
        st.info('这周没有个人课程时段。')
        return
    people = get_people()
    names = {}
    for row in slots:
        names.setdefault(clean_name(row['name']).casefold(), row['name'])
    mappings = {}
    ambiguous = []
    for normalized, name in names.items():
        matches = [p for p in people if clean_name(p['name']).casefold() == normalized]
        if len(matches) > 1:
            ambiguous.append((normalized, name, matches))
    if ambiguous:
        with st.expander('处理同名学员', expanded=True):
            for normalized, name, matches in ambiguous:
                labels = {p['id']: f"S{p['id']:03d} · {p['name']}" for p in matches}
                key = hashlib.sha256(normalized.encode()).hexdigest()[:16]
                mappings[normalized] = st.selectbox(f'“{name}”对应哪位？', [None] + list(labels), format_func=lambda sid, labels=labels: labels.get(sid, '请选择'), key=f'slot_match_{token}_{key}')
    rows = [{**r, 'kind': kind, 'student_id': mappings.get(clean_name(r['name']).casefold())} for r in slots]
    preview_rows = preview(rows)
    context = hashlib.sha256(repr((year, kind, mappings)).encode()).hexdigest()[:16]
    table = [{
        '导入': True,
        '姓名': row['name'],
        '日期': row['starts_at'][:10],
        '开始': row['starts_at'][11:16],
        '结束': row['ends_at'][11:16],
        '地点': row.get('location') or None,
        '档案': row.get('student_action', '待确认同名学员'),
    } for row in preview_rows]
    edited = st.data_editor(
        table, hide_index=True,
        disabled=['姓名','日期','开始','结束','档案'],
        column_config={'地点': st.column_config.SelectboxColumn('地点', options=['Meadows','Grange Club'])},
        key=f'slot_editor_{token}_{context}',
    )
    edits = edited.to_dict('records') if hasattr(edited, 'to_dict') else edited
    selected = [{**row, 'location': edit['地点'] or ''} for row, edit in zip(rows, edits) if edit['导入']]
    checked = preview(selected)
    eligible = [r for r in checked if r['result'] == '可以导入']
    new_names = {clean_name(r['name']).casefold() for r in eligible if r['student_id'] < 0}
    skipped = [r for r in checked if r['result'] != '可以导入']
    if skipped:
        with st.expander(f'{len(skipped)} 条会跳过 / 需处理'):
            for row in skipped:
                st.write(f"{row['name']} · {row['starts_at'].replace('T',' ')}：{row['result']}")
    st.caption(f'将添加 {len(eligible)} 节课，新建 {len(new_names)} 位学员。确认后添加课程。')
    if st.button(f'确认导入 {len(eligible)} 节', type='primary', disabled=not eligible, key=f'slot_submit_{token}'):
        try:
            result = save(selected)
        except Exception as error:
            st.error(f'整批未保存：{error}')
        else:
            added = sum(r['result'] == '已添加' for r in result)
            created = sum(r['new_student'] for r in result)
            skipped_count = len(result) - added
            st.session_state['slot_import_notice'] = f'已添加 {added} 节课，新建 {created} 位学员，跳过 {skipped_count} 条。可在本周课程和学员档案中查看。'
            st.rerun()

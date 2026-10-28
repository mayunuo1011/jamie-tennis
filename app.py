import streamlit as st
from student_history import render_student_history

from database import (
    init_db,
    get_students,
    get_student,
    save_student,
    add_package,
    get_packages,
)


st.set_page_config(
    page_title="教学工作台",
    page_icon="🎾",
    layout="centered",
)

init_db()

st.markdown("""
    <style>
    .block-container {
        max-width: 900px;
        padding-top: 2rem;
    }
    .stButton > button {
        min-height: 44px;
        border-radius: 12px;
    }
    </style>
""", unsafe_allow_html=True)


def show_message():
    message = st.session_state.pop("success_message", None)

    if message:
        st.success(message)


def refresh(message):
    st.session_state["success_message"] = message
    st.rerun()


def student_form(student=None):
    is_edit = student is not None
    data = student or {}

    form_key = (
        f"edit_student_{student['id']}"
        if is_edit
        else "new_student"
    )

    with st.form(form_key, clear_on_submit=not is_edit):
        name = st.text_input(
            "姓名 / 称呼 *",
            value=data.get("name", ""),
        )

        left, right = st.columns(2)

        with left:
            contact = st.text_input(
                "联系方式（选填）",
                value=data.get("contact", ""),
            )

            level = st.text_input(
                "网球水平",
                value=data.get("level", ""),
                placeholder="例如：初学、能稳定对拉",
            )

        with right:
            source = st.text_input(
                "通过什么途径找到你",
                value=data.get("source", ""),
                placeholder="例如：朋友介绍、小红书",
            )

        pain_points = st.text_area(
            "技术痛点",
            value=data.get("pain_points", ""),
        )

        goals = st.text_area(
            "教学目标",
            value=data.get("goals", ""),
        )

        remarks = st.text_area(
            "备注",
            value=data.get("remarks", ""),
        )

        submitted = st.form_submit_button(
            "保存修改" if is_edit else "添加学员",
            type="primary",
        )

    if submitted:
        try:
            student_id = save_student(
                name,
                contact,
                source,
                level,
                pain_points,
                goals,
                remarks,
                student_id=data.get("id"),
            )
        except Exception as error:
            st.error(str(error))
        else:
            refresh(f"已保存学员 S{student_id:03d}")


def package_table(packages):
    if not packages:
        st.info("暂无购买记录。")
        return

    records = [
        {
            "课包编号": f"P{p['id']:03d}",
            "学员编号": f"S{p['student_id']:03d}",
            "学员": p["student_name"],
            "类型": p["kind"],
            "购买日期": p["purchased_at"],
            "购买节数": p["purchased"],
            "剩余节数": p["remaining"],
        }
        for p in packages
    ]

    st.dataframe(
        records,
        hide_index=True,
        use_container_width=True,
    )


st.title("教学工作台")
st.caption("学员档案 · 购买记录 · 分类课时余额")

show_message()

page = st.radio(
    "页面",
   ["今日课程", "学员管理", "课包管理", "课程管理", "上传课表"],
    horizontal=True,
    label_visibility="collapsed",
)

st.divider()


if page == "学员管理":
    all_students = get_students()

    left, right = st.columns(2)

    left.metric("学员总数", len(all_students))
    right.metric(
        "有剩余课时的学员",
        sum(
            1
            for s in all_students
            if s["private_balance"] + s["practice_balance"] > 0
        ),
    )

    with st.expander(
        "＋ 添加学员",
        expanded=not bool(all_students),
    ):
        student_form()

    search = st.text_input(
        "搜索学员",
        placeholder="输入姓名或编号，例如 Alex、S001",
    )

    results = get_students(search)

    if not results:
        st.info("没有找到学员。可以清空搜索，或添加新学员。")

    else:
        st.dataframe(
            [
                {
                    "编号": f"S{s['id']:03d}",
                    "姓名": s["name"],
                    "水平": s["level"] or "未填写",
                    "私教剩余": s["private_balance"],
                    "陪打剩余": s["practice_balance"],
                }
                for s in results
            ],
            hide_index=True,
            use_container_width=True,
        )

        student_map = {s["id"]: s for s in results}

        selected_id = st.selectbox(
            "选择学员，查看或编辑档案",
            options=list(student_map),
            format_func=lambda sid: (
                f"S{sid:03d} · {student_map[sid]['name']}"
            ),
        )

        student = get_student(selected_id)
        summary = student_map[selected_id]

        st.subheader(
            f"{student['name']} · S{student['id']:03d}"
        )

        left, right = st.columns(2)

        left.metric(
            "私教剩余",
            f"{summary['private_balance']} 节",
        )

        right.metric(
            "陪打剩余",
            f"{summary['practice_balance']} 节",
        )

        detail_tab, purchase_tab, history_tab = st.tabs(
            ["档案 / 编辑", "购买记录", "课程与训练记录"]
        )

        with detail_tab:
            student_form(student)

        with purchase_tab:
            package_table(get_packages(selected_id))

        with history_tab:
            render_student_history(selected_id)


elif page == "课包管理":
    st.subheader("录入购买")

    all_students = get_students()

    if not all_students:
        st.info("请先到「学员管理」添加学员。")

    else:
        student_map = {s["id"]: s for s in all_students}

        with st.form("purchase_form", clear_on_submit=True):
            student_id = st.selectbox(
                "购买学员",
                options=list(student_map),
                format_func=lambda sid: (
                    f"S{sid:03d} · {student_map[sid]['name']}"
                ),
            )

            left, right = st.columns(2)

            with left:
                kind = st.selectbox(
                    "课程类型",
                    ["私教", "陪打"],
                )

                quantity = st.number_input(
                    "购买节数",
                    min_value=1,
                    max_value=10000,
                    value=10,
                    step=1,
                )

            with right:
                purchased_at = st.date_input("购买日期")

            submitted = st.form_submit_button(
                "确认录入这次购买",
                type="primary",
            )

        if submitted:
            try:
                add_package(
                    student_id,
                    kind,
                    purchased_at.isoformat(),
                    quantity,
                )
            except Exception as error:
                st.error(str(error))
            else:
                refresh("购买已录入，余额已更新。")

        st.divider()
        st.subheader("全部购买记录")

        package_table(get_packages())


from database import delete_package


if page == "课包管理":
    packages = get_packages()

    if packages:
        st.divider()

        with st.expander("删除录错的课包"):
            package_map = {
                package["id"]: package
                for package in packages
            }

            selected_package_id = st.selectbox(
                "选择要删除的购买记录",
                options=list(package_map),
                format_func=lambda pid: (
                    f"P{pid:03d} · "
                    f"{package_map[pid]['student_name']} · "
                    f"{package_map[pid]['kind']} · "
                    f"{package_map[pid]['purchased']} 节 · "
                    f"{package_map[pid]['purchased_at']}"
                ),
                key="delete_package_selector",
            )

            selected = package_map[selected_package_id]

            if selected["remaining"] != selected["purchased"]:
                st.warning(
                    "这条课包已经使用过，暂不支持直接删除。"
                )

            else:
                st.warning(
                    f"删除后，将移除这条购买记录，"
                    f"并减少该学员 "
                    f"{selected['remaining']} 节"
                    f"{selected['kind']}余额。"
                    "删除后无法在页面内恢复。"
                )

                confirmed = st.checkbox(
                    "我确认这条记录录入错误，要删除",
                    key=f"confirm_delete_{selected_package_id}",
                )

                if st.button(
                    "删除这条课包",
                    disabled=not confirmed,
                    key=f"delete_package_{selected_package_id}",
                ):
                    try:
                        delete_package(selected_package_id)
                    except Exception as error:
                        st.error(str(error))
                    else:
                        refresh("课包已删除，余额已更新。")

from datetime import datetime, time

from database import (
    init_lessons,
    add_lesson,
    get_lessons,
    save_lesson_note,
    complete_lesson,
)


init_lessons()


if page == "课程管理":
    st.subheader("课程管理")

    all_students = get_students()

    with st.expander(
        "＋ 添加课程",
        expanded=not bool(get_lessons()),
    ):
        if not all_students:
            st.info("请先到「学员管理」添加学员。")

        else:
            student_map = {
                student["id"]: student
                for student in all_students
            }

            student_id = st.selectbox(
                "学员",
                options=list(student_map),
                format_func=lambda sid: (
                    f"S{sid:03d} · "
                    f"{student_map[sid]['name']}"
                ),
                key="new_lesson_student",
            )

            kind = st.selectbox(
                "课程类型",
                ["私教", "陪打"],
                key="new_lesson_kind",
            )

            selected_student = student_map[student_id]

            packages = [
                package
                for package in get_packages(student_id)
                if package["kind"] == kind
            ]

            balance = sum(
                package["remaining"]
                for package in packages
            )

            can_add = balance > 0

            if not packages:
                st.error(
                    f"{selected_student['name']}尚未购买"
                    f"{kind}课程，不能添加。"
                    "请先到「课包管理」录入购买。"
                )

            elif balance == 0:
                st.error(
                    f"{selected_student['name']}的"
                    f"{kind}课已用完，剩余 0 节。"
                    "请先录入新的购买。"
                )

            else:
                st.success(
                    f"{selected_student['name']} · "
                    f"{kind}剩余 {balance} 节"
                )

            with st.form(
                "add_lesson_form",
                clear_on_submit=True,
            ):
                lesson_date = st.date_input("上课日期")

                left, right = st.columns(2)

                with left:
                    start_time = st.time_input(
                        "开始时间",
                        value=time(14, 0),
                    )

                with right:
                    end_time = st.time_input(
                        "结束时间",
                        value=time(15, 0),
                    )

                location = st.text_input(
                    "地点",
                    placeholder="例如：Court 2",
                )

                create = st.form_submit_button(
                    "添加课程" if can_add else "无可用课时，不能添加",
                    type="primary",
                    disabled=not can_add,
                )

            if create:
                starts_at = datetime.combine(
                    lesson_date,
                    start_time,
                ).isoformat(timespec="minutes")

                ends_at = datetime.combine(
                    lesson_date,
                    end_time,
                ).isoformat(timespec="minutes")

                try:
                    add_lesson(
                        student_id,
                        kind,
                        starts_at,
                        ends_at,
                        location,
                    )
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh("课程已添加。")

    st.divider()

    left, right = st.columns(2)

    with left:
        selected_day = st.date_input(
            "查看哪一天的课程",
            key="view_lesson_date",
        )

    with right:
        show_all = st.checkbox("查看全部日期")

    lessons = get_lessons(
        None if show_all else selected_day.isoformat()
    )

    completed_count = sum(
        lesson["status"] == "已完成"
        for lesson in lessons
    )

    pending_count = sum(
        lesson["status"] == "待上课"
        for lesson in lessons
    )

    left, right = st.columns(2)
    left.metric("待上课", pending_count)
    right.metric("已完成", completed_count)

    if not lessons:
        st.info("没有课程。可以在上方添加。")

    for lesson in lessons:
        lesson_id = lesson["id"]

        with st.container(border=True):
            left, right = st.columns([3, 1])

            with left:
                st.subheader(
                    f"{lesson['student_name']} · "
                    f"{lesson['kind']}课"
                )

            with right:
                st.write(lesson["status"])

            start = datetime.fromisoformat(
                lesson["starts_at"]
            )

            end = datetime.fromisoformat(
                lesson["ends_at"]
            )

            st.write(
                f"🕒 {start:%Y-%m-%d}　"
                f"{start:%H:%M}–{end:%H:%M}"
            )

            st.write(f"📍 {lesson['location']}")

            st.caption(
                f"L{lesson_id:03d} · "
                f"S{lesson['student_id']:03d} · "
                f"{lesson['kind']}当前剩余 "
                f"{lesson['balance']} 节"
            )

            if (
                lesson["status"] == "已完成"
                and not lesson["note"].strip()
            ):
                st.warning("课已完成，课后记录待补充。")

            with st.form(f"lesson_record_{lesson_id}"):
                note = st.text_area(
                    "课后记录（可以稍后补）",
                    value=lesson["note"],
                    placeholder="训练内容、表现……",
                )

                next_goal = st.text_input(
                    "下次目标",
                    value=lesson["next_goal"],
                )

                save_note = st.form_submit_button(
                    "仅保存记录",
                )

                finish = False

                if lesson["status"] == "待上课":
                    finish = st.form_submit_button(
                        "完成课程并扣除 1 节",
                        type="primary",
                    )

            if save_note:
                try:
                    save_lesson_note(
                        lesson_id,
                        note,
                        next_goal,
                    )
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh("记录已保存，没有扣课。")

            if finish:
                try:
                    complete_lesson(
                        lesson_id,
                        note,
                        next_goal,
                    )
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh("课程已完成，已扣除 1 节。")
from database import cancel_lesson, settle_unpaid_lesson


if page == "课程管理":
    st.divider()
    st.subheader("取消课程 / 待扣课")

    all_lessons = get_lessons()

    pending_lessons = [
        lesson
        for lesson in all_lessons
        if lesson["status"] == "待上课"
    ]

    with st.expander("取消一节课程"):
        if not pending_lessons:
            st.info("没有待上课的课程。")

        else:
            lesson_map = {
                lesson["id"]: lesson
                for lesson in pending_lessons
            }

            selected_lesson_id = st.selectbox(
                "选择要取消的课程",
                options=list(lesson_map),
                format_func=lambda lid: (
                    f"L{lid:03d} · "
                    f"{lesson_map[lid]['student_name']} · "
                    f"{lesson_map[lid]['kind']} · "
                    f"{lesson_map[lid]['starts_at'].replace('T', ' ')}"
                ),
                key="cancel_lesson_selector",
            )

            selected_lesson = lesson_map[selected_lesson_id]

            student_absences = sum(
                lesson["absence"]
                for lesson in all_lessons
                if lesson["student_id"]
                == selected_lesson["student_id"]
            )

            st.caption(
                f"该学员目前累计缺课 {student_absences} 次。"
                "不足 24 小时取消计缺课；"
                "第 1 次不扣，第 2 次起每次扣 1 节。"
            )

            with st.form(
                f"cancel_lesson_form_{selected_lesson_id}"
            ):
                reason = st.selectbox(
                    "取消理由",
                    ["学员取消", "天气", "教练原因", "其他"],
                )

                details = st.text_input(
                    "补充说明（选填）",
                    placeholder="例如：下雨、临时有事",
                )

                left, right = st.columns(2)

                with left:
                    notice_date = st.date_input(
                        "实际收到取消通知的日期",
                    )

                with right:
                    notice_time = st.time_input(
                        "实际收到取消通知的时间",
                        value=datetime.now().time().replace(
                            second=0,
                            microsecond=0,
                        ),
                    )

                manual_charge = st.checkbox(
                    "天气 / 教练原因 / 其他：本次扣 1 节"
                )

                st.caption(
                    "选择「学员取消」时按缺课规则自动判断，"
                    "不受上面勾选框影响。"
                )

                cancel_submitted = st.form_submit_button(
                    "确认取消",
                    type="primary",
                )

            if cancel_submitted:
                notified_at = datetime.combine(
                    notice_date,
                    notice_time,
                ).isoformat(timespec="minutes")

                try:
                    message = cancel_lesson(
                        selected_lesson_id,
                        reason,
                        notified_at,
                        manual_charge,
                        details,
                    )
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh(message)

    unpaid_lessons = [
        lesson
        for lesson in all_lessons
        if lesson["charge_due"] and not lesson["charged"]
    ]

    st.write(f"应扣但未扣：{len(unpaid_lessons)} 节")

    for lesson in unpaid_lessons:
        with st.container(border=True):
            st.write(
                f"**{lesson['student_name']} · "
                f"{lesson['kind']} · "
                f"L{lesson['id']:03d}**"
            )

            st.caption(
                lesson["starts_at"].replace("T", " ")
            )

            st.write(
                f"未扣课 · 当前余额 {lesson['balance']} 节"
            )

            if st.button(
                "补扣 1 节",
                key=f"settle_unpaid_{lesson['id']}",
            ):
                try:
                    message = settle_unpaid_lesson(
                        lesson["id"]
                    )
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh(message)

    cancelled_lessons = [
        lesson
        for lesson in all_lessons
        if lesson["status"] == "已取消"
    ]

    with st.expander("查看取消记录"):
        if not cancelled_lessons:
            st.info("暂无取消记录。")

        else:
            records = []

            for lesson in cancelled_lessons:
                if lesson["charged"]:
                    billing = "已扣 1 节"
                elif lesson["charge_due"]:
                    billing = "应扣但未扣"
                else:
                    billing = "不扣课"

                records.append({
                    "课程": f"L{lesson['id']:03d}",
                    "学员": lesson["student_name"],
                    "类型": lesson["kind"],
                    "上课时间": lesson["starts_at"].replace(
                        "T", " "
                    ),
                    "取消理由": lesson["reason"],
                    "通知时间": lesson["notified_at"],
                    "计缺课": (
                        "是" if lesson["absence"] else "否"
                    ),
                    "扣课结果": billing,
                })

            st.dataframe(
                records,
                hide_index=True,
                use_container_width=True,
            )
from dashboard import render_dashboard


if page == "今日课程":
    render_dashboard()

from database import delete_lesson


if page == "课程管理":
    st.divider()

    with st.expander("删除误添加的课程"):
        deletable_lessons = [
            lesson
            for lesson in get_lessons()
            if lesson["status"] == "待上课"
            and not lesson["charged"]
            and not lesson["charge_due"]
        ]

        if not deletable_lessons:
            st.info("没有可以删除的待上课课程。")

        else:
            delete_map = {
                lesson["id"]: lesson
                for lesson in deletable_lessons
            }

            delete_id = st.selectbox(
                "选择要删除的课程",
                options=list(delete_map),
                format_func=lambda lid: (
                    f"L{lid:03d} · "
                    f"{delete_map[lid]['student_name']} · "
                    f"{delete_map[lid]['kind']} · "
                    f"{delete_map[lid]['starts_at'].replace('T', ' ')} · "
                    f"{delete_map[lid]['location']}"
                ),
                key="delete_lesson_selector",
            )

            selected = delete_map[delete_id]

            st.warning(
                "删除后，这节课程及已填写的课后记录会一起移除，"
                "无法在页面内恢复。不会改变课时余额。"
            )

            confirmed = st.checkbox(
                "确认这是误添加的课程，要删除",
                key=f"confirm_delete_lesson_{delete_id}",
            )

            if st.button(
                "删除这节课程",
                disabled=not confirmed,
                key=f"delete_lesson_button_{delete_id}",
            ):
                try:
                    delete_lesson(delete_id)
                except Exception as error:
                    st.error(str(error))
                else:
                    refresh("课程已删除，课时余额未改变。")
from upload_page import render_upload_page


if page == "上传课表":
    render_upload_page()

from datetime import datetime

import streamlit as st

from database import get_lessons, save_lesson_note


def render_student_history(student_id):
    lessons = [
        lesson
        for lesson in get_lessons()
        if lesson["student_id"] == student_id
    ]

    completed_count = sum(
        lesson["status"] == "已完成"
        for lesson in lessons
    )

    absence_count = sum(
        lesson["absence"]
        for lesson in lessons
    )

    unpaid_count = sum(
        lesson["charge_due"] and not lesson["charged"]
        for lesson in lessons
    )

    left, middle, right = st.columns(3)

    left.metric("实际上课", f"{completed_count} 次")
    middle.metric("累计缺课", f"{absence_count} 次")
    right.metric("应扣但未扣", f"{unpaid_count} 节")

    completed_with_goal = [
        lesson
        for lesson in lessons
        if lesson["status"] == "已完成"
        and lesson["next_goal"].strip()
    ]

    if completed_with_goal:
        latest = max(
            completed_with_goal,
            key=lambda lesson: (
                lesson["starts_at"],
                lesson["id"],
            ),
        )

        st.info(
            "最近一次训练的下次目标："
            + latest["next_goal"]
        )

    selected_status = st.radio(
        "筛选课程",
        ["全部", "已完成", "待上课", "已取消"],
        horizontal=True,
        key=f"history_status_{student_id}",
    )

    visible_lessons = [
        lesson
        for lesson in lessons
        if selected_status == "全部"
        or lesson["status"] == selected_status
    ]

    if not visible_lessons:
        st.info("暂无符合条件的课程。")
        return

    for lesson in visible_lessons:
        lesson_id = lesson["id"]

        start = datetime.fromisoformat(
            lesson["starts_at"]
        )

        end = datetime.fromisoformat(
            lesson["ends_at"]
        )

        with st.container(border=True):
            st.write(
                f"**{start:%Y-%m-%d} · "
                f"{lesson['kind']}课**"
            )

            st.caption(
                f"L{lesson_id:03d} · "
                f"{start:%H:%M}–{end:%H:%M} · "
                f"{lesson['location']}"
            )

            if lesson["charged"]:
                billing = "已扣 1 节"
            elif lesson["charge_due"]:
                billing = "应扣但未扣"
            elif lesson["status"] == "待上课":
                billing = "尚未结算"
            else:
                billing = "不扣课"

            status = lesson["status"]

            if lesson["absence"]:
                status += " · 缺课"

            st.caption(f"{status} · {billing}")

            if lesson["reason"]:
                st.write("取消理由：" + lesson["reason"])

            st.write("**训练记录**")

            if lesson["note"].strip():
                st.write(lesson["note"])
            else:
                st.caption("尚未填写")

            st.write("**下次目标**")

            if lesson["next_goal"].strip():
                st.write(lesson["next_goal"])
            else:
                st.caption("尚未填写")

            with st.expander("补充 / 修改记录"):
                with st.form(
                    f"student_history_note_{student_id}_{lesson_id}"
                ):
                    note = st.text_area(
                        "课后记录",
                        value=lesson["note"],
                    )

                    next_goal = st.text_input(
                        "下次目标",
                        value=lesson["next_goal"],
                    )

                    submitted = st.form_submit_button(
                        "保存记录",
                        type="primary",
                    )

                if submitted:
                    try:
                        save_lesson_note(
                            lesson_id,
                            note,
                            next_goal,
                        )
                    except Exception as error:
                        st.error(str(error))
                    else:
                        st.session_state["success_message"] = (
                            "训练记录已更新，课时余额未改变。"
                        )
                        st.rerun()
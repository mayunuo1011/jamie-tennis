from datetime import date, datetime

import streamlit as st

from database import (
    get_lessons,
    save_lesson_note,
    complete_lesson,
    cancel_lesson,
    settle_unpaid_lesson,
)


def run_action(function, *args):
    try:
        result = function(*args)
    except Exception as error:
        st.error(str(error))
    else:
        st.session_state["success_message"] = (
            result if isinstance(result, str) else "已保存。"
        )
        st.rerun()


def lesson_card(lesson, all_lessons, section):
    lesson_id = lesson["id"]
    key = f"dashboard_{section}_{lesson_id}"

    start = datetime.fromisoformat(lesson["starts_at"])
    end = datetime.fromisoformat(lesson["ends_at"])

    with st.container(border=True):
        st.subheader(
            f"{lesson['student_name']} · {lesson['kind']}课"
        )

        status = lesson["status"]

        if lesson["absence"]:
            status += " · 缺课"

        if lesson["charged"]:
            status += " · 已扣 1 节"
        elif lesson["charge_due"]:
            status += " · 应扣但未扣"

        st.caption(f"L{lesson_id:03d} · {status}")

        st.write(
            f"🕒 {start:%m月%d日}　"
            f"{start:%H:%M}–{end:%H:%M}"
        )
        st.write(f"📍 {lesson['location']}")
        st.write(
            f"{lesson['kind']}剩余："
            f"**{lesson['balance']} 节**"
        )

        previous = [
            item
            for item in all_lessons
            if item["student_id"] == lesson["student_id"]
            and item["status"] == "已完成"
            and item["starts_at"] < lesson["starts_at"]
            and item["next_goal"].strip()
        ]

        if previous:
            latest = max(
                previous,
                key=lambda item: (
                    item["starts_at"],
                    item["id"],
                ),
            )
            st.info("上次目标：" + latest["next_goal"])

        if lesson["status"] == "已取消":
            st.caption("取消理由：" + lesson["reason"])

        with st.form(f"{key}_notes"):
            note = st.text_area(
                "课后记录",
                value=lesson["note"],
                placeholder="训练重点、表现……可以稍后补。",
            )

            next_goal = st.text_input(
                "下次目标",
                value=lesson["next_goal"],
            )

            finish = False

            if lesson["status"] == "待上课":
                finish = st.form_submit_button(
                    "完成课程并扣除 1 节",
                    type="primary",
                )

            save = st.form_submit_button("仅保存记录")

        if finish:
            run_action(
                complete_lesson,
                lesson_id,
                note,
                next_goal,
            )

        if save:
            run_action(
                save_lesson_note,
                lesson_id,
                note,
                next_goal,
            )

        if lesson["charge_due"] and not lesson["charged"]:
            st.warning("这节课需要扣课，但尚未扣除。")

            if st.button(
                "补扣 1 节",
                key=f"{key}_settle",
            ):
                run_action(
                    settle_unpaid_lesson,
                    lesson_id,
                )

        if lesson["status"] == "待上课":
            with st.expander("取消这节课"):
                with st.form(f"{key}_cancel"):
                    reason = st.selectbox(
                        "取消理由",
                        ["学员取消", "天气", "教练原因", "其他"],
                    )

                    details = st.text_input("补充说明")

                    notice_date = st.date_input(
                        "实际收到通知的日期",
                        value=date.today(),
                    )

                    notice_time = st.time_input(
                        "实际收到通知的时间",
                        value=datetime.now().time().replace(
                            second=0,
                            microsecond=0,
                        ),
                    )

                    manual_charge = st.checkbox(
                        "非学员取消：本次扣 1 节"
                    )

                    st.caption(
                        "学员取消按 24 小时和累计缺课规则判断；"
                        "其他原因由你选择是否扣课。"
                    )

                    cancel = st.form_submit_button(
                        "确认取消课程"
                    )

                if cancel:
                    notified_at = datetime.combine(
                        notice_date,
                        notice_time,
                    ).isoformat(timespec="minutes")

                    run_action(
                        cancel_lesson,
                        lesson_id,
                        reason,
                        notified_at,
                        manual_charge,
                        details,
                    )


def render_dashboard():
    st.subheader("今日课程")

    selected_day = st.date_input(
        "查看日期",
        value=date.today(),
        key="dashboard_date",
    )

    all_lessons = get_lessons()

    day_lessons = sorted(
        [
            lesson
            for lesson in all_lessons
            if lesson["starts_at"][:10]
            == selected_day.isoformat()
        ],
        key=lambda lesson: lesson["starts_at"],
    )

    missing_notes = [
        lesson
        for lesson in all_lessons
        if lesson["status"] == "已完成"
        and not lesson["note"].strip()
    ]

    unpaid_lessons = [
        lesson
        for lesson in all_lessons
        if lesson["charge_due"] and not lesson["charged"]
    ]

    left, middle, right = st.columns(3)

    left.metric(
        "当天待上课",
        sum(
            lesson["status"] == "待上课"
            for lesson in day_lessons
        ),
    )
    middle.metric("待补记录", len(missing_notes))
    right.metric("未扣课", len(unpaid_lessons))

    st.caption("待补记录和未扣课包含所有日期。")

    today_tab, notes_tab, unpaid_tab = st.tabs([
        "当天课程",
        f"待补记录 · {len(missing_notes)}",
        f"未扣课 · {len(unpaid_lessons)}",
    ])

    with today_tab:
        if not day_lessons:
            st.info("当天没有课程。去「课程管理」添加。")

        for lesson in day_lessons:
            lesson_card(lesson, all_lessons, "day")

    with notes_tab:
        if not missing_notes:
            st.success("没有遗漏的课后记录。")

        for lesson in missing_notes:
            lesson_card(lesson, all_lessons, "notes")

    with unpaid_tab:
        if not unpaid_lessons:
            st.success("没有待扣课。")

        for lesson in unpaid_lessons:
            lesson_card(lesson, all_lessons, "unpaid")
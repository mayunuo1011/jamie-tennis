import os
from html import escape

import streamlit as st

import user_store as store
import user_service as svc
from user_theme import apply_theme
from user_views import weekly_page, students_page, management_page, upcoming_cards, sticky_notes, dispatch_dialog

COACH_NAME=os.environ.get('TENNIS_COACH_NAME','小罗')
st.set_page_config(page_title=f'{COACH_NAME}的教学工作台',page_icon='🎾',layout='wide',initial_sidebar_state='expanded')

@st.cache_resource
def prepare(path):
    store.configure(path)
    return store.initialize()

try:
    prepare(str(store.DB_PATH.resolve()))
except Exception as error:
    st.error(f'启动失败，未进入工作台：{error}')
    st.stop()

apply_theme()
st.markdown(f'<header class="workspace-title"><strong>{escape(COACH_NAME)}的教学工作台</strong><small>JAMIE TENNIS</small></header>',unsafe_allow_html=True)
with st.sidebar:
    st.title('Jamie Tennis')
    st.caption('教练工作台')
    page=st.radio('导航',['本周课程','学员档案','课程管理','课表导入'],label_visibility='collapsed',key='workspace_nav')

notice=st.session_state.pop('user_notice',None)
if notice:
    st.success(notice)

@st.fragment(run_every=30)
def clock():
    minute=svc.now_london().strftime('%Y-%m-%d %H:%M')
    previous=st.session_state.get('clock_minute')
    st.session_state['clock_minute']=minute
    if previous and previous!=minute and page=='本周课程' and not st.session_state.get('dialog_active'):
        st.rerun()

clock()
main,right=st.columns([3.2,1.05],gap='large')
with main:
    if page=='本周课程':
        weekly_page()
    elif page=='学员档案':
        students_page()
    elif page=='课程管理':
        management_page()
    else:
        from user_upload_page import render_upload_page
        render_upload_page()
with right:
    if page=='本周课程':
        upcoming_cards()
    sticky_notes()

dispatch_dialog()

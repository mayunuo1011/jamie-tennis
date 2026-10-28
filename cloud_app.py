"""Single hosted app with separate staff and student routes and shared durable DB."""
import os
import streamlit as st


def load_settings():
    if not os.environ.get('JAMIE_DATABASE_URL'):
        try:
            value=st.secrets.get('JAMIE_DATABASE_URL','')
            schema=st.secrets.get('JAMIE_DB_SCHEMA','jamie_tennis')
        except Exception:value='';schema='jamie_tennis'
        if not value:raise RuntimeError('网站尚未配置云端数据库，请联系维护人员。')
        os.environ['JAMIE_DATABASE_URL']=value;os.environ['JAMIE_DB_SCHEMA']=schema
    import pg_store
    pg_store.settings();pg_store.schema_name()


def run():
    try:
        load_settings()
        import ops_store as store
        with store.connect() as con:
            versions={r[0] for r in con.execute('SELECT version FROM ops_migrations')}
            if versions!=set(range(1,8)):raise RuntimeError('云端数据尚未完成迁移，请联系维护人员。')
    except Exception:
        st.set_page_config(page_title='Jamie Tennis',page_icon='🎾')
        st.title('Jamie Tennis');st.error('网站尚未就绪，请联系维护人员。');return
    portal=st.query_params.get('portal','')
    if portal not in ('staff','student'):
        st.set_page_config(page_title='Jamie Tennis',page_icon='🎾',layout='centered')
        st.title('Jamie Tennis')
        st.markdown('### [🎾 学员预约](?portal=student)')
        st.markdown('### [教练与运营登录](?portal=staff)')
        return
    previous=st.session_state.get('_cloud_portal')
    if previous and previous!=portal:
        for key in list(st.session_state):st.session_state.pop(key,None)
    st.session_state['_cloud_portal']=portal
    if portal=='staff':
        from ops_ui_app import run as render
    else:
        from student_calendar_app import run as render
    render()


if __name__=='__main__':run()

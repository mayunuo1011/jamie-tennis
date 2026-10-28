"""Teacher drafts, Jamie weekly publication, and passwordless account delivery."""
from datetime import datetime,time,timedelta
import streamlit as st
import ops_auth as auth
import ops_store as store
import ops_credits as credits
import ops_service as svc
import ops_booking as api
import student_auth
import booking_calendar as calendar
import user_service as rules


COLORS=('#dceaff','#e2f3e8','#ffedcf','#efe3fa','#ffe1e7','#d9f3f2')


def close_slot_dialog():st.session_state.pop('ops_slot_cell',None)


def render_slot_grid(token,rows,start):
    """A real button per entry: same-time teachers keep independent colors/actions."""
    times=sorted({f'{h:02}:00' for h in range(9,20)}|{r['starts_at'][11:16] for r in rows})
    names={r['coach_id']:r['coach_name'] for r in rows}
    styles=''.join(f'[class*="st-key-slot_teacher_{cid}_"] button {{background:{COLORS[cid%len(COLORS)]};color:#173b65;border:0;}}' for cid in names)
    st.markdown('<style>'+styles+'''
    .st-key-slot_calendar_scroll{overflow-x:auto}
    .st-key-slot_calendar_grid{min-width:1050px}
    .st-key-slot_calendar_grid [data-testid="stHorizontalBlock"]{flex-wrap:nowrap!important}
    .st-key-slot_calendar_grid [data-testid="stColumn"]{min-width:0!important;width:0!important;flex:1 1 0%!important}
    .st-key-slot_calendar_grid [data-testid="stColumn"]:first-child{flex:.6 1 0%!important}
    [class*="st-key-slot_cell_"]{padding:4px!important}
    </style>''',unsafe_allow_html=True)
    with st.container(key='slot_calendar_scroll'):
        with st.container(key='slot_calendar_grid'):
            header=st.columns([.6]+[1]*7)
            header[0].caption('时间')
            for i,col in enumerate(header[1:]):col.markdown(f"**周{'一二三四五六日'[i]} {(start+timedelta(days=i)):%m/%d}**")
            for stamp in times:
                cols=st.columns([.6]+[1]*7)
                cols[0].caption(stamp)
                cells=[[r for r in rows if r['starts_at'][:10]==(start+timedelta(days=i)).isoformat() and r['starts_at'][11:16]==stamp] for i in range(7)]
                height=74+max(map(len,cells),default=0)*65
                for i,col in enumerate(cols[1:]):
                    day=start+timedelta(days=i)
                    with col.container(height=height,border=True,key=f'slot_cell_{day}_{stamp}'):
                        for row in cells[i]:
                            with st.container(key=f"slot_teacher_{row['coach_id']}_{row['id']}"):
                                if st.button(f"{row['coach_name']} · {row['kind']}\n{row['starts_at'][11:16]}–{row['ends_at'][11:16]} · {row['state']}",key=f"ops_grid_slot_{row['id']}",use_container_width=True):
                                    st.session_state['ops_slot_cell']={'day':str(day),'time':stamp,'slot':row['id']}
                        if st.button('＋',key=f'ops_grid_add_{day}_{stamp}',use_container_width=True,help='添加这个时间的空闲时段'):
                            st.session_state['ops_slot_cell']={'day':str(day),'time':stamp}


@st.dialog('预约时段',width='large',on_dismiss=close_slot_dialog)
def slot_dialog(token):
    from datetime import date
    user=auth.current_user(token);cell=st.session_state['ops_slot_cell']
    if cell.get('slot'):
        row=next((r for r in api.staff_slots(token) if r['id']==cell['slot']),None)
        if not row:st.info('这个时段已变化，请刷新。');return
        st.subheader(f"{row['coach_name']} · {row['kind']}")
        st.write(f"{row['starts_at'].replace('T',' ')}–{row['ends_at'][11:16]} · {row['location']}")
        st.caption('已约' if row['booked'] else '已发布' if row['published'] else '草稿，学员暂不可见')
        if api.slot_editable(token,row['coach_id']):
            with st.popover('关闭这个时段'):
                st.write('关闭后不能再预约，已有预约不会取消。修改时间请关闭后重新添加。')
                if st.button('确认关闭',key='ops_grid_close'):
                    try:api.close_slot(token,row['id'],row['revision'])
                    except ValueError as error:st.error(str(error))
                    else:close_slot_dialog();st.rerun()
        else:st.caption('Luo的空闲时段由Luo自己维护；Jamie负责检查和发布。')
        return
    teachers=svc.coaches(token)
    choices={f"coach:{c['id']}":c['name'] for c in teachers if api.slot_editable(token,c['id'])}
    if user['role'] in credits.ROLES:
        existing={c['name'] for c in teachers}
        choices.update({f'external:{name}':name for name in api.EXTERNAL_COACHES if name not in existing})
    if not choices:st.info('没有可维护的教练。');return
    initial=datetime.fromisoformat(cell['day']+'T'+cell['time'])
    with st.form('ops_grid_draft'):
        coach=st.selectbox('教练',list(choices),format_func=choices.get)
        kind=st.selectbox('课程类型',['私教','陪打'],format_func=lambda k:'陪练' if k=='陪打' else k)
        day=st.date_input('日期',value=date.fromisoformat(cell['day']))
        a,b=st.columns(2);begin=a.time_input('开始',initial.time());end=b.time_input('结束',(initial+timedelta(hours=1)).time())
        place=st.selectbox('地点',['Meadows','Grange Club','其他'])
        other=st.text_input('其他地点（选择其他时填写）')
        saved=st.form_submit_button('保存草稿',type='primary')
    if saved:
        try:
            kwargs={'external_name':coach.split(':',1)[1]} if coach.startswith('external:') else {'coach_id':int(coach.split(':')[1])}
            api.publish(token,kind,datetime.combine(day,begin).isoformat(timespec='minutes'),datetime.combine(day,end).isoformat(timespec='minutes'),other if place=='其他' else place,**kwargs)
        except (ValueError,RuntimeError) as error:st.error(str(error))
        else:
            close_slot_dialog();st.session_state['user_notice']='已保存草稿，Jamie可同步查看并发布。';st.rerun()


def render(token):
    user=auth.current_user(token)
    st.subheader('开放预约')
    start=calendar.controls('ops_booking_calendar')
    st.caption('Jamie检查整周日历后发布。')
    rows=[r for r in api.staff_slots(token) if start.isoformat()<=r['starts_at'][:10]<(start+timedelta(days=7)).isoformat() and not r['closed']]
    for row in rows:row['state']='已约' if row['booked'] else '已发布' if row['published'] else '草稿'
    render_slot_grid(token,rows,start)
    if user['role'] in credits.ROLES:
        count=sum(not r['published'] for r in rows)
        with st.popover(f'发布本周草稿时段（{count}）',disabled=count==0):
            st.write('确认发布日历中本周所有草稿？发布后学员可预约。')
            if st.button('确认发布',type='primary',key='ops_confirm_publish_week'):
                try:n=api.publish_week(token,start)
                except ValueError as error:st.error(str(error))
                else:st.session_state['user_notice']=f'已发布{n}个时段。';calendar.reset();st.rerun()
    if st.session_state.get('ops_slot_cell'):
        slot_dialog(token)
    if user['role'] not in credits.ROLES:return
    with st.expander('学员预约账号'):
        render_student_accounts(token)


def render_student_accounts(token):
    st.subheader('学员账号')
    st.caption('账号私下发给本人，学员输入账号即可进入，无密码。')
    people=svc.list_students(token)
    if not people:return
    selected=st.selectbox('选择学员',[p['id'] for p in people],format_func=lambda i:next(f"S{p['id']:03d} · {p['name']}" for p in people if p['id']==i),key='ops_calendar_account_sid')
    with store.connect() as con:
        auth.require(con,token,credits.ROLES)
        account=con.execute('SELECT login_name,active FROM student_accounts WHERE student_id=?',(selected,)).fetchone()
    if account and account['login_name']:
        st.code(account['login_name']);st.caption('已启用' if account['active'] else '已停用')
        with st.popover('重新生成账号'):
            st.write('确认更换账号？旧账号立即失效，已登录设备会退出，需要重新私发。')
            if st.button('确认重新生成',key='ops_rotate_alias'):
                student_auth.provision(token,selected,True);st.rerun()
    elif st.button('生成学员账号',key='ops_provision_alias'):
        student_auth.provision(token,selected);st.rerun()
    if account:
        if st.button('停用账号' if account['active'] else '启用账号',key='ops_alias_toggle'):
            student_auth.set_active(token,selected,not bool(account['active']));st.rerun()

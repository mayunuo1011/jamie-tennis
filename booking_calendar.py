"""Shared weekly calendar, returning cell selection without embedding credentials."""
from datetime import date,datetime,timedelta
import pandas as pd
import streamlit as st
import user_service as rules


def reset():
    st.session_state.pop('student_dialog',None)
    st.session_state.pop('ops_slot_cell',None)
    st.session_state['calendar_epoch']=st.session_state.get('calendar_epoch',0)+1


def controls(prefix):
    key=f'{prefix}_week'
    st.session_state.setdefault(key,rules.week_start())
    nav_key=f'{prefix}_shift_{st.session_state.get("calendar_epoch",0)}'
    def move():
        selected=st.session_state.get(nav_key,[])
        choice=selected[0] if selected else None
        if choice=='本周':st.session_state[key]=rules.week_start()
        elif choice:st.session_state[key]+=timedelta(days=-7 if choice=='上一周' else 7)
        reset()
    st.pills('切换周',['上一周','本周','下一周'],selection_mode='multi',default=[],key=nav_key,on_change=move,label_visibility='collapsed')
    selected=st.date_input('选择所在周',value=st.session_state[key],key=f'{prefix}_week_input_{st.session_state[key]}',label_visibility='collapsed')
    start=rules.week_start(selected)
    if start!=st.session_state[key]:st.session_state[key]=start;reset();st.rerun()
    st.caption(f'{start:%Y-%m-%d} — {start+timedelta(days=6):%Y-%m-%d} · 英国伦敦时间')
    return start


def frame(rows,start):
    days=[start+timedelta(days=i) for i in range(7)]
    headers=[f"周{'一二三四五六日'[i]} {day:%m/%d}" for i,day in enumerate(days)]
    hours=[int(r['starts_at'][11:13]) for r in rows]
    times=sorted({f'{h:02}:00' for h in range(min(hours,default=9),max(hours,default=18)+1)}|{r['starts_at'][11:16] for r in rows})
    matrix=[];lookup={}
    for index,t in enumerate(times):
        item={'时间':t}
        for day,header in zip(days,headers):
            selected=[r for r in rows if r['starts_at'][:10]==day.isoformat() and r['starts_at'][11:16]==t]
            lookup[(index,header)]=selected
            item[header]='\n'.join(f"{r['state']} · {r['coach_name']} {r['kind']}" for r in selected)
        matrix.append(item)
    return pd.DataFrame(matrix),lookup


def render(rows,start,key):
    table,lookup=frame(rows,start)
    def color(value):
        if not value:return ''
        if any(v.startswith('可预约') for v in value.split('\n')):return 'background-color:#e9f7ef;color:#1b6344'
        if '我的预约' in value:return 'background-color:#e4efff;color:#16457b'
        if '草稿' in value:return 'background-color:#fff4db;color:#775418'
        return 'background-color:#ededf0;color:#797d86'
    selection=st.dataframe(table.style.map(color,subset=list(table.columns)[1:]),hide_index=True,
      width='stretch',height=min(620,48+len(table)*56),row_height=56,on_select='rerun',selection_mode='single-cell',
      key=f'{key}_{start}_{st.session_state.get("calendar_epoch",0)}',
      column_config={'时间':st.column_config.TextColumn(width=65),**{c:st.column_config.TextColumn(width=190) for c in list(table.columns)[1:]}})
    cells=getattr(selection.selection,'cells',[])
    if cells:return lookup.get(tuple(cells[0]),[])
    return []

import streamlit as st


def apply_theme():
    st.markdown('''<style>
    .stApp{background:#f7f9fc;color:#182b43}
    .block-container{max-width:1560px;padding:6rem 2.2rem 4rem}
    [data-testid="stHeader"]{background:transparent}
    [data-testid="stToolbar"]{visibility:hidden}
    [data-testid="stSidebar"]{background:#102a4c;border-right:1px solid #102a4c}
    [data-testid="stSidebar"] h1,[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,[data-testid="stSidebar"] span{color:#fff}
    [data-testid="stSidebar"] [role="radiogroup"]{gap:.6rem}
    [data-testid="stSidebar"] [role="radiogroup"] label{padding:.7rem .6rem;border-radius:10px;margin:0}
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked){background:#28466a;border-left:3px solid #cf384c}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] p{color:#bac9dc}
    h1,h2,h3{color:#142f51;letter-spacing:-.025em}
    h1{font-size:1.8rem!important}h2{font-size:1.4rem!important}h3{font-size:1.08rem!important}
    .workspace-title{position:fixed;top:1.5rem;right:2.4rem;z-index:90;text-align:right;background:#f7f9fcf2;padding:.4rem .6rem;border-right:4px solid #c9344c}
    .workspace-title strong{font-size:1.45rem;color:#142f51;font-weight:750}
    .workspace-title small{display:block;font-size:.69rem;letter-spacing:.13em;color:#7b8b9e;margin-top:.15rem}
    [data-testid="stMetric"]{background:white;border:1px solid #e2e8f0;border-radius:13px;padding:1rem 1.2rem}
    [data-testid="stMetricValue"]{font-size:1.85rem;color:#163d69}
    [data-testid="stCaptionContainer"]{color:#718298}
    [data-testid="stForm"]{background:white;border:1px solid #e1e8f0;border-radius:14px}
    [data-testid="stExpander"]{background:white;border-radius:12px}
    [data-testid="stVerticalBlockBorderWrapper"]>div{border-color:#e1e8f0!important;border-radius:15px!important}
    button[kind="primary"],button[data-testid="stBaseButton-primary"]{background:#173b65;border-color:#173b65;color:white;border-radius:10px;min-height:42px}
    button[kind="primary"]:hover{background:#255583;border-color:#255583;color:white}
    button:disabled{opacity:.5}
    [data-testid="stButton"] button,[data-testid="stFormSubmitButton"] button{border-radius:10px;min-height:40px}
    [data-testid="stTextArea"] textarea,[data-testid="stTextInput"] input{background:#f7f9fc;color:#182b43}
    .upcoming-name{font-weight:700;font-size:.98rem;color:#173b65;margin-bottom:.35rem}
    .upcoming-time{font-size:.85rem;color:#78869b;margin-bottom:.45rem}
    .court{display:inline-block;font-size:.75rem;background:#edf1f6;color:#526681;border-radius:6px;padding:.2rem .45rem}
    .court.meadows{background:#fff0f1;color:#995365}.court.grange{background:#eaf3fc;color:#346b99}
    .st-key-notes_panel{position:fixed;right:2.2rem;bottom:1.5rem;width:clamp(240px,21vw,310px);max-height:48vh;overflow:auto;z-index:80;background:#fff;border:1px solid #dbe3ee;border-top:3px solid #c9344c;border-radius:13px;padding:1rem;box-shadow:0 8px 28px #173b6510}
    .st-key-notes_panel [data-testid="stTextArea"] textarea{background:#fffcf5}
    [data-testid="stDialog"] h2{color:#173b65}
    [data-testid="stDialog"] [data-testid="stVerticalBlock"]{gap:.8rem}
    [data-testid="stDataFrame"]{border-radius:12px;overflow:hidden}
    @media(max-width:1100px){.st-key-notes_panel{position:static;width:auto;max-height:none;margin-top:1rem}.workspace-title{font-size:1rem;right:1.2rem}.block-container{padding:6rem 1.1rem 3rem}}
    @media(max-width:650px){.workspace-title strong{font-size:1.08rem}.block-container{padding:5.6rem .8rem 2rem}[data-testid="stMetric"]{padding:.6rem}[data-testid="stMetricValue"]{font-size:1.3rem}}
    </style>''',unsafe_allow_html=True)

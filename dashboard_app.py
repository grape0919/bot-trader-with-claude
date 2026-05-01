"""
Streamlit 대시보드 — 실시간 봇 상태 + 거래 히스토리 + 지표 스냅샷
SQLite + JSON 상태 파일에서 데이터 로드
"""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

STATE_DIR = Path('/app/state')
DB_PATH = STATE_DIR / 'trading.db'
LIVE_STATE = STATE_DIR / 'live_state.json'
INDICATORS = STATE_DIR / 'indicators.json'

st.set_page_config(page_title='Bitget Bot', layout='wide', page_icon='📊')

# 30초마다 자동 새로고침
if 'last_refresh' not in st.session_state:
    st.session_state.last_refresh = datetime.now()
if (datetime.now() - st.session_state.last_refresh).seconds >= 30:
    st.session_state.last_refresh = datetime.now()
    st.rerun()


@st.cache_data(ttl=10)
def load_state():
    if LIVE_STATE.exists():
        return json.loads(LIVE_STATE.read_text())
    return {}


@st.cache_data(ttl=10)
def load_indicators():
    if INDICATORS.exists():
        return json.loads(INDICATORS.read_text())
    return {}


@st.cache_data(ttl=10)
def query_df(sql: str, params=()):
    if not DB_PATH.exists():
        return pd.DataFrame()
    with sqlite3.connect(DB_PATH) as c:
        return pd.read_sql_query(sql, c, params=params)


# ─── 헤더 ─────────────────────────────────────────────────────────────
state = load_state()
indicators = load_indicators()

st.title('📊 Bitget Trading Bot')
st.caption(f'마지막 갱신: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} (30초마다 자동 새로고침)')

# 메트릭 카드
peak = state.get('peak_balance', 0)
withdrawn = state.get('total_withdrawn', 0)
reloads = state.get('total_reloads', 0)
starting = state.get('starting_balance', 0)

# 최신 잔고 (DB에서)
latest = query_df('SELECT balance, equity, drawdown_pct, positions FROM balance_history ORDER BY ts DESC LIMIT 1')
balance = float(latest['balance'].iloc[0]) if not latest.empty else 0
equity = float(latest['equity'].iloc[0]) if not latest.empty else 0
dd = float(latest['drawdown_pct'].iloc[0]) if not latest.empty else 0
n_pos = int(latest['positions'].iloc[0]) if not latest.empty else 0

profit = (balance + withdrawn) - starting if starting > 0 else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric('잔고', f'${balance:.2f}', delta=f'{profit:+.2f}')
c2.metric('Equity (포지션 포함)', f'${equity:.2f}')
c3.metric('드로다운', f'{dd:.1f}%', delta_color='inverse')
c4.metric('포지션', f'{n_pos} / 3')
c5.metric('누적 출금 / 청산', f'${withdrawn:.0f} / {reloads}회')

# ─── 탭 ───────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(['📈 잔고 추이', '💼 거래 내역', '📊 페어별 지표', '🚫 스킵 분석'])

with tab1:
    bh = query_df("""
        SELECT ts/1000 AS ts_sec, balance, equity, drawdown_pct
        FROM balance_history
        WHERE ts > ?
        ORDER BY ts
    """, ((int(datetime.now().timestamp()) - 7 * 86400) * 1000,))
    if not bh.empty:
        bh['ts'] = pd.to_datetime(bh['ts_sec'], unit='s')
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=bh['ts'], y=bh['balance'], name='잔고', line=dict(color='#1f77b4')))
        fig.add_trace(go.Scatter(x=bh['ts'], y=bh['equity'], name='Equity', line=dict(color='#2ca02c', dash='dot')))
        fig.update_layout(height=400, hovermode='x unified', margin=dict(l=0, r=0, t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader('드로다운')
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=bh['ts'], y=-bh['drawdown_pct'], fill='tozeroy', line=dict(color='#d62728')))
        fig2.update_layout(height=200, margin=dict(l=0, r=0, t=20, b=0), yaxis_title='DD %')
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info('아직 잔고 히스토리가 없습니다 (봇 첫 사이클 후 수집됨)')

with tab2:
    trades = query_df("""
        SELECT entry_ts/1000 AS entry_sec, exit_ts/1000 AS exit_sec,
               symbol, side, mode, entry_price, exit_price, pnl, reason, status
        FROM trades
        ORDER BY entry_ts DESC
        LIMIT 100
    """)
    if not trades.empty:
        trades['진입'] = pd.to_datetime(trades['entry_sec'], unit='s')
        trades['청산'] = pd.to_datetime(trades['exit_sec'], unit='s', errors='coerce')
        closed = trades[trades['status'] == 'closed']

        ca, cb, cc, cd = st.columns(4)
        wins = (closed['pnl'] > 0).sum()
        losses = (closed['pnl'] <= 0).sum()
        wr = wins / max(len(closed), 1) * 100
        total_pnl = closed['pnl'].sum()
        ca.metric('전체 거래', f'{len(trades)}건 (진행중 {(trades["status"]=="open").sum()})')
        cb.metric('완결 승률', f'{wr:.1f}%', f'{wins}승 {losses}패')
        cc.metric('합산 PnL', f'${total_pnl:+.2f}')
        cd.metric('평균 PnL', f'${closed["pnl"].mean() if len(closed) else 0:+.2f}')

        # PnL 분포
        if len(closed) > 0:
            fig = px.histogram(closed, x='pnl', nbins=30, color='side', barmode='overlay',
                                color_discrete_map={'long': '#2ca02c', 'short': '#d62728'})
            fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0))
            st.plotly_chart(fig, use_container_width=True)

        st.subheader('거래 목록')
        display = trades[['진입', '청산', 'symbol', 'side', 'mode',
                          'entry_price', 'exit_price', 'pnl', 'reason', 'status']].copy()
        display.columns = ['진입', '청산', '심볼', '방향', '모드', '진입가', '청산가', 'PnL', '사유', '상태']
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info('아직 거래가 없습니다')

with tab3:
    if indicators:
        rows = []
        for sym, info in indicators.items():
            snap = info.get('snap', {})
            rows.append({
                '심볼': sym.split('/')[0],
                '가격': snap.get('price', 0),
                'RSI': snap.get('rsi'),
                'ADX': snap.get('adx'),
                'ATR%': snap.get('atr_pct'),
                'ROC5': snap.get('roc5'),
                'Vol비': snap.get('vol_ratio'),
                '시그널': info.get('signal', '') or '-',
                '모드': info.get('mode', '') or '-',
                '사유': info.get('reason', ''),
            })
        df = pd.DataFrame(rows)

        def color_rsi(v):
            if pd.isna(v): return ''
            if v >= 70: return 'background-color: #ffcccc'
            if v <= 30: return 'background-color: #ccffcc'
            return ''

        styled = df.style.map(color_rsi, subset=['RSI'])
        st.dataframe(styled, use_container_width=True, hide_index=True)
    else:
        st.info('지표 스냅샷이 아직 없습니다')

with tab4:
    skip = query_df("""
        SELECT reason, COUNT(*) as 횟수
        FROM skip_log
        WHERE ts > ?
        GROUP BY reason
        ORDER BY 횟수 DESC
        LIMIT 20
    """, ((int(datetime.now().timestamp()) - 86400) * 1000,))
    if not skip.empty:
        st.subheader('최근 24시간 스킵 사유')
        # 사유에서 첫 부분만 추출 (수치 제거)
        skip['카테고리'] = skip['reason'].str.split(' \\(').str[0].str.split(' but ').str[0]
        agg = skip.groupby('카테고리')['횟수'].sum().sort_values(ascending=False).reset_index()
        fig = px.bar(agg, x='횟수', y='카테고리', orientation='h')
        fig.update_layout(height=400, margin=dict(l=0, r=0, t=20, b=0))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info('스킵 로그가 아직 없습니다')

# ─── 사이드바 ─────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader('🤖 봇 상태')
    if state:
        st.text(f'시작 잔고: ${starting:.2f}')
        st.text(f'시드: $40 (3배 도달 시 출금)')
        wlog = state.get('withdrawal_log', [])
        if wlog:
            st.subheader('출금 이력')
            for w in wlog[-5:]:
                st.text(f'${w.get("amount", 0):.2f} @ {w.get("ts", "?")}')
    if st.button('🔄 즉시 새로고침'):
        st.cache_data.clear()
        st.rerun()

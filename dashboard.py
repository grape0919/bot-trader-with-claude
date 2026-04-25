"""
페이퍼 봇 대시보드 — paper_state.json + indicators.json 을 읽어 HTML 렌더링.
봇이 매 사이클 자동 갱신. 브라우저에서 dashboard.html 열어 확인.
"""
import json
import os
from datetime import datetime
from pathlib import Path

DASHBOARD_HTML = 'dashboard.html'
INDICATORS_JSON = 'indicators.json'


def _rsi_color(v):
    if v is None: return '#888'
    if v < 30: return '#22c55e'   # 과매도 (매수 기회) - 초록
    if v < 40: return '#84cc16'   # 약세
    if v < 60: return '#94a3b8'   # 중립 - 회색
    if v < 70: return '#f97316'   # 강세
    return '#ef4444'               # 과매수 - 빨강


def _adx_color(v):
    if v is None: return '#888'
    if v < 20:  return '#64748b'   # 무추세
    if v < 25:  return '#94a3b8'   # 약한 추세
    if v < 30:  return '#eab308'   # 중간
    return '#22c55e'                # 강한 추세 - 초록


def _bar(v, max_v=100, color='#3b82f6', width=80):
    """진행 바 생성"""
    if v is None: return ''
    pct = max(0, min(100, v / max_v * 100))
    return f'''<div style="display:inline-block;width:{width}px;height:8px;background:#1e293b;border-radius:4px;vertical-align:middle;margin-left:4px">
        <div style="width:{pct}%;height:100%;background:{color};border-radius:4px"></div>
    </div>'''


def render_dashboard(state, indicators, config_data):
    balance = state.get('balance', 0)
    initial = state.get('initial', 40)
    withdrawn = state.get('total_withdrawn', 0)
    reloads = state.get('total_reloads', 0)
    positions = state.get('positions', {})
    trade_log = state.get('trade_log', [])
    withdrawal_log = state.get('withdrawal_log', [])
    total_trades = state.get('total_trades', 0)
    win_trades = state.get('win_trades', 0)

    reload_cost = initial * reloads
    net_profit = balance + withdrawn - initial - reload_cost
    roi = net_profit / initial * 100 if initial else 0
    win_rate = win_trades / total_trades * 100 if total_trades else 0

    # 심볼 상태 카드
    pair_cards = ''
    for sym, data in indicators.items():
        short_sym = sym.split('/')[0]
        snap = data.get('snap', {})
        reason = data.get('reason', '-')
        signal = data.get('signal', '')
        rsi = snap.get('rsi')
        adx = snap.get('adx')
        price = snap.get('price', 0)
        roc5 = snap.get('roc5', 0)
        vol_r = snap.get('vol_ratio', 0)
        atr_pct = snap.get('atr_pct', 0)

        # 진입 가능성 색상
        if signal:
            border = '3px solid #22c55e'
            status_text = f'진입: {signal.upper()}'
            status_color = '#22c55e'
        elif rsi and 35 <= rsi <= 45:
            border = '2px solid #eab308'
            status_text = '🟡 롱 임박'
            status_color = '#eab308'
        elif rsi and 55 <= rsi <= 65:
            border = '2px solid #eab308'
            status_text = '🟡 숏 임박'
            status_color = '#eab308'
        else:
            border = '1px solid #334155'
            status_text = '대기'
            status_color = '#64748b'

        # 포지션 중이면 강조
        in_pos = sym in positions
        if in_pos:
            pos = positions[sym]
            side = pos.get('side', '')
            entry = pos.get('entry_price', 0)
            margin = pos.get('margin', 0)
            lev = pos.get('leverage', 0)
            mode = pos.get('mode', '')
            pnl_pct = ((price - entry) / entry * 100) if side == 'long' else ((entry - price) / entry * 100)
            pnl_color = '#22c55e' if pnl_pct >= 0 else '#ef4444'
            pos_info = f'''
                <div style="margin-top:8px;padding:8px;background:#1e293b;border-radius:4px">
                    <div style="font-weight:bold;color:{pnl_color}">
                        📌 {side.upper()} ({mode}) {lev}x
                    </div>
                    <div style="font-size:12px;color:#cbd5e1">
                        진입: ${entry:.4f} | 증거금: ${margin:.2f}<br>
                        현재 PnL: <span style="color:{pnl_color}">{pnl_pct:+.2f}%</span>
                    </div>
                </div>'''
            border = f'3px solid {pnl_color}'
            status_text = f'📌 포지션 보유'
        else:
            pos_info = ''

        rsi_val = f'{rsi:.1f}' if rsi else 'N/A'
        adx_val = f'{adx:.1f}' if adx else 'N/A'

        pair_cards += f'''
        <div class="pair-card" style="border:{border}">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div style="font-size:18px;font-weight:bold">{short_sym}</div>
                <div style="color:{status_color};font-size:12px;font-weight:bold">{status_text}</div>
            </div>
            <div style="color:#cbd5e1;font-size:13px;margin-top:4px">${price:,.4f}</div>

            <div style="margin-top:10px">
                <div style="font-size:11px;color:#94a3b8">
                    RSI <span style="color:{_rsi_color(rsi)};font-weight:bold;font-size:13px">{rsi_val}</span>
                    {_bar(rsi, 100, _rsi_color(rsi))}
                </div>
                <div style="font-size:11px;color:#94a3b8;margin-top:4px">
                    ADX <span style="color:{_adx_color(adx)};font-weight:bold;font-size:13px">{adx_val}</span>
                    {_bar(adx, 60, _adx_color(adx))}
                </div>
                <div style="font-size:11px;color:#94a3b8;margin-top:4px">
                    ROC5: <span style="color:{'#22c55e' if roc5 and roc5<0 else '#ef4444' if roc5 else '#888'}">{roc5:.2f}%</span>
                    | Vol: <span style="color:{'#22c55e' if vol_r and vol_r>1.3 else '#888'}">{vol_r:.2f}x</span>
                    | ATR: {atr_pct:.2f}%
                </div>
            </div>

            {pos_info}

            <div style="margin-top:8px;font-size:10px;color:#64748b;border-top:1px solid #334155;padding-top:6px">
                {reason}
            </div>
        </div>
        '''

    # 최근 거래 테이블
    trades_rows = ''
    for t in reversed(trade_log[-10:]):
        pnl = t.get('pnl', 0)
        pnl_color = '#22c55e' if pnl >= 0 else '#ef4444'
        sign = '+' if pnl >= 0 else ''
        ts = t.get('time', '')[:16].replace('T', ' ')
        trades_rows += f'''
        <tr>
            <td style="padding:4px 8px;color:#94a3b8;font-size:11px">{ts}</td>
            <td style="padding:4px 8px">{t.get('symbol', '').split('/')[0]}</td>
            <td style="padding:4px 8px">{t.get('side', '').upper()} ({t.get('mode', '')})</td>
            <td style="padding:4px 8px;color:{pnl_color};text-align:right">{sign}${pnl:.4f}</td>
            <td style="padding:4px 8px;font-size:11px;color:#94a3b8">{t.get('reason', '')}</td>
        </tr>
        '''

    # 출금 내역
    withdraws_rows = ''
    for w in reversed(withdrawal_log[-5:]):
        ts = w.get('time', '')[:16].replace('T', ' ')
        withdraws_rows += f'''
        <tr>
            <td style="padding:4px 8px;color:#94a3b8;font-size:11px">{ts}</td>
            <td style="padding:4px 8px;color:#22c55e;text-align:right;font-weight:bold">${w.get('amount', 0):.2f}</td>
            <td style="padding:4px 8px;text-align:right;color:#cbd5e1">${w.get('cumulative', 0):.2f}</td>
        </tr>
        '''

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    pnl_color = '#22c55e' if net_profit >= 0 else '#ef4444'
    balance_color = '#22c55e' if balance >= initial else '#ef4444'

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="10">
    <title>Trading Bot Dashboard</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box }}
        body {{
            background: #0f172a; color: #e2e8f0;
            font-family: -apple-system, 'Menlo', monospace; padding: 20px;
        }}
        .header {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px; margin-bottom: 24px;
        }}
        .stat-card {{
            background: #1e293b; padding: 16px; border-radius: 8px;
            border-left: 4px solid #3b82f6;
        }}
        .stat-label {{ color: #94a3b8; font-size: 12px; margin-bottom: 6px }}
        .stat-value {{ font-size: 20px; font-weight: bold }}
        .section-title {{
            font-size: 16px; font-weight: bold; margin: 20px 0 12px 0;
            color: #cbd5e1; border-bottom: 1px solid #334155; padding-bottom: 6px;
        }}
        .pair-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 12px;
        }}
        .pair-card {{
            background: #1e293b; padding: 12px; border-radius: 8px;
        }}
        table {{
            width: 100%; background: #1e293b; border-radius: 8px;
            overflow: hidden; border-collapse: collapse;
        }}
        th {{
            background: #334155; padding: 8px; text-align: left; font-size: 12px;
            color: #cbd5e1;
        }}
        td {{ border-top: 1px solid #334155 }}
        .footer {{
            margin-top: 20px; color: #64748b; font-size: 11px; text-align: center;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="stat-card" style="border-color:{balance_color}">
            <div class="stat-label">현재 잔고</div>
            <div class="stat-value" style="color:{balance_color}">${balance:.2f}</div>
        </div>
        <div class="stat-card" style="border-color:#22c55e">
            <div class="stat-label">누적 출금</div>
            <div class="stat-value" style="color:#22c55e">${withdrawn:.2f}</div>
            <div style="color:#94a3b8;font-size:11px;margin-top:4px">{len(withdrawal_log)}회</div>
        </div>
        <div class="stat-card" style="border-color:{'#ef4444' if reloads else '#64748b'}">
            <div class="stat-label">청산 횟수</div>
            <div class="stat-value" style="color:{'#ef4444' if reloads else '#64748b'}">{reloads}회</div>
            <div style="color:#94a3b8;font-size:11px;margin-top:4px">손실 -${reload_cost:.2f}</div>
        </div>
        <div class="stat-card" style="border-color:{pnl_color}">
            <div class="stat-label">순수익 (ROI)</div>
            <div class="stat-value" style="color:{pnl_color}">${net_profit:+.2f}</div>
            <div style="color:{pnl_color};font-size:11px;margin-top:4px">{roi:+.1f}%</div>
        </div>
        <div class="stat-card" style="border-color:#3b82f6">
            <div class="stat-label">거래 / 승률</div>
            <div class="stat-value" style="color:#3b82f6">{total_trades}</div>
            <div style="color:#94a3b8;font-size:11px;margin-top:4px">{win_rate:.0f}% ({win_trades}승)</div>
        </div>
    </div>

    <div class="section-title">📊 페어 현황 (실시간 지표)</div>
    <div class="pair-grid">{pair_cards}</div>

    <div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-top:20px">
        <div>
            <div class="section-title">📜 최근 거래 ({len(trade_log)}건 중 최신 10개)</div>
            <table>
                <thead>
                    <tr>
                        <th>시간</th><th>심볼</th><th>방향</th>
                        <th style="text-align:right">PnL</th><th>사유</th>
                    </tr>
                </thead>
                <tbody>{trades_rows if trades_rows else '<tr><td colspan="5" style="padding:20px;text-align:center;color:#64748b">거래 없음</td></tr>'}</tbody>
            </table>
        </div>
        <div>
            <div class="section-title">💰 출금 내역</div>
            <table>
                <thead>
                    <tr>
                        <th>시간</th>
                        <th style="text-align:right">금액</th>
                        <th style="text-align:right">누적</th>
                    </tr>
                </thead>
                <tbody>{withdraws_rows if withdraws_rows else '<tr><td colspan="3" style="padding:20px;text-align:center;color:#64748b">출금 없음</td></tr>'}</tbody>
            </table>
        </div>
    </div>

    <div class="footer">
        🤖 페이퍼 트레이딩 모드 | 마지막 갱신: {now} | 10초마다 자동 새로고침<br>
        전략: 멀티시그널 (추세 RSI풀백 + 급락반등 ROC + 횡보 RSI극단) · 3x레버 · 3배 출금 모델
    </div>
</body>
</html>
'''
    Path(DASHBOARD_HTML).write_text(html, encoding='utf-8')


def update_from_files():
    """paper_state.json + indicators.json 읽어 대시보드 갱신"""
    state = {}
    if os.path.exists('paper_state.json'):
        state = json.loads(Path('paper_state.json').read_text(encoding='utf-8'))
    indicators = {}
    if os.path.exists(INDICATORS_JSON):
        indicators = json.loads(Path(INDICATORS_JSON).read_text(encoding='utf-8'))
    render_dashboard(state, indicators, None)


if __name__ == '__main__':
    update_from_files()
    print(f'Dashboard: file://{os.path.abspath(DASHBOARD_HTML)}')

"""
볼륨 필터(F3) 적용 전략 — 최근 90일 상세 검증
거래 내역, 월별 성과, 시그널별 분포, 드로다운 프로필
"""
import time
import numpy as np
import pandas as pd
import ccxt
from datetime import datetime

import config
from data_cache import load_ohlcv
from strategy import calculate_indicators

SEED = 40.0
DAYS = 90  # 최근 3개월
LEV_T = [(1.0, 60), (2.0, 45), (3.0, 30), (float('inf'), 21)]
LEV_R = [(1.0, 45), (2.0, 30), (3.0, 21), (float('inf'), 15)]

exchange = ccxt.bitget({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})


def _adx(h, l, c, length=14):
    pdm = h.diff().clip(lower=0); mdm = (-l.diff()).clip(lower=0)
    pdm[pdm <= mdm] = 0; mdm[mdm <= pdm] = 0
    pc = c.shift(1)
    tr = pd.concat([h-l, (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=length, adjust=False).mean()
    pdi = 100 * (pdm.ewm(span=length, adjust=False).mean() / atr.replace(0, np.nan))
    mdi = 100 * (mdm.ewm(span=length, adjust=False).mean() / atr.replace(0, np.nan))
    dx = (abs(pdi-mdi) / (pdi+mdi).replace(0, np.nan)) * 100
    return dx.ewm(span=length, adjust=False).mean()


def prepare(df):
    df = calculate_indicators(df)
    df['adx'] = _adx(df['high'], df['low'], df['close'])
    df['vol_ma'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma'].replace(0, np.nan)
    df['roc_5'] = (df['close'] / df['close'].shift(5) - 1) * 100
    # 볼륨 필터용: 최근 5봉 vs 이전 20봉
    df['vol_5'] = df['volume'].rolling(5).mean()
    df['vol_20'] = df['volume'].rolling(20).mean()
    df['vol_strong'] = df['vol_5'] > df['vol_20'] * 1.2
    return df


def check_signal(curr, prev):
    adx = float(curr['adx']) if not pd.isna(curr['adx']) else 0
    rsi = float(curr['rsi']); prev_rsi = float(prev['rsi'])
    roc5 = float(curr['roc_5']) if not pd.isna(curr['roc_5']) else 0
    vol_r = float(curr['vol_ratio']) if not pd.isna(curr['vol_ratio']) else 0

    if adx > 30:
        if curr['ema_fast'] > curr['ema_slow'] > curr['ema_trend']:
            if prev_rsi < 40 and rsi >= 40: return 'long', 'trend', 1.5, LEV_T
        if curr['ema_fast'] < curr['ema_slow'] < curr['ema_trend']:
            if prev_rsi > 60 and rsi <= 60: return 'short', 'trend', 1.5, LEV_T
    if adx <= 30:
        if roc5 < -2 and rsi < 35 and vol_r >= 1.3: return 'long', 'roc', 1.2, LEV_R
        if roc5 > 2 and rsi > 65 and vol_r >= 1.3: return 'short', 'roc', 1.2, LEV_R
    if adx <= 25:
        if prev_rsi < 30 and rsi >= 30: return 'long', 'range', 1.5, LEV_R
        if prev_rsi > 70 and rsi <= 70: return 'short', 'range', 1.5, LEV_R
    return None, '', 0, None


def check_exit(rsi, mode, side):
    if mode == 'trend':
        return (side == 'long' and rsi >= 70) or (side == 'short' and rsi <= 30)
    elif mode == 'roc':
        return (side == 'long' and rsi >= 50) or (side == 'short' and rsi <= 50)
    else:
        return (side == 'long' and rsi >= 55) or (side == 'short' and rsi <= 45)


def backtest(cache, use_volume_filter, label):
    balance = SEED; peak = SEED
    positions = {}
    trades = []
    equity_hist = []
    min_len = min(len(df) for df in cache.values())

    for i in range(55, min_len):
        for sym in cache:
            df = cache[sym]
            curr = df.iloc[i]; prev = df.iloc[i-1]
            h = float(curr['high']); l = float(curr['low'])
            p = float(curr['close']); atr = float(curr['atr'])
            if pd.isna(atr) or atr <= 0: continue
            rsi = float(curr['rsi'])

            if sym in positions:
                pos = positions[sym]
                hit = None
                if pos['s'] == 'long':
                    if l <= pos['sl']: hit, ep, r = True, pos['sl'], 'SL'
                else:
                    if h >= pos['sl']: hit, ep, r = True, pos['sl'], 'SL'
                if not hit and check_exit(rsi, pos.get('md', ''), pos['s']):
                    hit, ep, r = True, p, 'RSI청산'
                if hit:
                    diff = (ep - pos['e']) if pos['s'] == 'long' else (pos['e'] - ep)
                    pnl = (diff / pos['e']) * (pos['a'] * pos['e'])
                    balance += pos['m'] + pnl
                    peak = max(peak, balance)
                    trades.append({
                        'exit_ts': curr['ts'], 'sym': sym.split('/')[0], 'side': pos['s'],
                        'md': pos.get('md', ''), 'entry': pos['e'], 'exit': ep,
                        'pnl': pnl, 'reason': r, 'entry_ts': pos['entry_ts'],
                    })
                    del positions[sym]
                continue

            if len(positions) >= 3: continue
            needed = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
            if any(pd.isna(curr.get(c, np.nan)) for c in needed): continue
            if pd.isna(prev.get('rsi', np.nan)): continue

            sig, md, sl_m, lev_t = check_signal(curr, prev)
            if not sig: continue

            # 볼륨 필터
            if use_volume_filter and not bool(curr.get('vol_strong', False)):
                continue

            atr_pct = atr / p * 100
            lev = lev_t[-1][1]
            for th, l2 in lev_t:
                if atr_pct < th: lev = l2; break
            lev = min(lev, 125)
            rem = 3 - len(positions)
            margin = balance / rem if rem > 0 else 0
            if margin < 1: continue
            amt = (margin * lev) / p
            sl = p - atr * sl_m if sig == 'long' else p + atr * sl_m
            positions[sym] = {'s': sig, 'e': p, 'a': amt, 'm': margin, 'sl': sl,
                              'md': md, 'entry_ts': curr['ts']}
            balance -= margin

        equity = balance + sum(x['m'] for x in positions.values())
        equity_hist.append(equity)

    # 미청산
    for sym, pos in list(positions.items()):
        p = float(cache[sym].iloc[min_len-1]['close'])
        diff = (p - pos['e']) if pos['s'] == 'long' else (pos['e'] - p)
        pnl = (diff / pos['e']) * (pos['a'] * pos['e'])
        balance += pos['m'] + pnl
        trades.append({
            'exit_ts': cache[sym].iloc[min_len-1]['ts'], 'sym': sym.split('/')[0],
            'side': pos['s'], 'md': pos.get('md', ''), 'entry': pos['e'], 'exit': p,
            'pnl': pnl, 'reason': '종료', 'entry_ts': pos['entry_ts'],
        })

    eq = np.array(equity_hist) if equity_hist else np.array([SEED])
    rm = np.maximum.accumulate(eq)
    mdd = float(((rm - eq) / rm * 100).max())

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    pnl_t = balance - SEED
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses and sum(t['pnl'] for t in losses) != 0 else 0

    return {'label': label, 'bal': balance, 'pnl': pnl_t, 'trades': trades,
            'wins': len(wins), 'losses': len(losses), 'wr': wr, 'pf': pf, 'mdd': mdd}


def print_detail(r):
    trades = r['trades']
    print(f"\n{'─' * 85}")
    print(f" {r['label']}")
    print(f"{'─' * 85}")
    print(f"  잔고: ${r['bal']:.2f} | 순수익: ${r['pnl']:+.2f} ({r['pnl']/SEED*100:+.1f}%)")
    print(f"  거래: {len(trades)}건 ({r['wins']}승 {r['losses']}패) | 승률: {r['wr']:.1f}% | PF: {r['pf']:.2f}")
    print(f"  최대낙폭: {r['mdd']:.1f}%")
    if trades:
        avg_w = np.mean([t['pnl'] for t in trades if t['pnl'] > 0]) if r['wins'] else 0
        avg_l = np.mean([t['pnl'] for t in trades if t['pnl'] <= 0]) if r['losses'] else 0
        print(f"  평균수익: ${avg_w:.4f} | 평균손실: ${avg_l:.4f}")

    # 모드별
    by_md = {}
    for t in trades:
        m = t['md']
        if m not in by_md: by_md[m] = {'n': 0, 'w': 0, 'pnl': 0}
        by_md[m]['n'] += 1; by_md[m]['pnl'] += t['pnl']
        if t['pnl'] > 0: by_md[m]['w'] += 1
    print('\n  [시그널별]')
    for m, v in sorted(by_md.items(), key=lambda x: -x[1]['pnl']):
        wrr = v['w'] / v['n'] * 100 if v['n'] else 0
        print(f"    {m:<8} {v['n']:>3}건  승률:{wrr:>5.1f}%  PnL:${v['pnl']:+7.2f}")

    # 심볼별
    by_sym = {}
    for t in trades:
        s = t['sym']
        if s not in by_sym: by_sym[s] = {'n': 0, 'w': 0, 'pnl': 0}
        by_sym[s]['n'] += 1; by_sym[s]['pnl'] += t['pnl']
        if t['pnl'] > 0: by_sym[s]['w'] += 1
    print('\n  [심볼별]')
    for s, v in sorted(by_sym.items(), key=lambda x: -x[1]['pnl']):
        wrr = v['w'] / v['n'] * 100 if v['n'] else 0
        print(f"    {s:<6} {v['n']:>3}건  승률:{wrr:>5.1f}%  PnL:${v['pnl']:+7.2f}")

    # 월별
    print('\n  [월별 성과]')
    monthly = {}
    for t in trades:
        key = pd.Timestamp(t['exit_ts']).strftime('%Y-%m')
        if key not in monthly: monthly[key] = {'n': 0, 'w': 0, 'pnl': 0}
        monthly[key]['n'] += 1; monthly[key]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly[key]['w'] += 1
    for key in sorted(monthly.keys()):
        v = monthly[key]
        wrr = v['w'] / v['n'] * 100 if v['n'] else 0
        print(f"    {key}: {v['n']:>3}건 ({v['w']}승) PnL:${v['pnl']:+7.2f} 승률:{wrr:.0f}%")


def main():
    print(f'데이터 로드 ({DAYS}일, 10페어)...')
    t0 = time.time()
    cache = {}
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', DAYS)
        cache[sym] = prepare(df)
    print(f'  {time.time()-t0:.1f}초 완료')

    print()
    print('=' * 85)
    print(f' 볼륨 필터 적용 검증 (최근 {DAYS}일)')
    print('=' * 85)

    r_base = backtest(cache, False, f'F0 기존 (볼륨 필터 없음)')
    r_vol = backtest(cache, True, f'F3 볼륨 필터 적용 (5봉 / 20봉 > 1.2)')

    # 간단 비교표
    print(f"  {'전략':<35} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 75)
    for r in [r_base, r_vol]:
        print(f"  {r['label']:<35} {len(r['trades']):>5} {r['wr']:>6.1f}% {r['pf']:>5.2f} ${r['pnl']:>+8.2f} {r['mdd']:>5.1f}%")

    # 상세
    print_detail(r_base)
    print_detail(r_vol)

    # 거래 비교
    print()
    print('=' * 85)
    print(' 볼륨 필터 효과 요약')
    print('=' * 85)
    print(f"  수익 증가  : ${r_base['pnl']:+.2f} → ${r_vol['pnl']:+.2f} (${r_vol['pnl']-r_base['pnl']:+.2f})")
    print(f"  승률 변화  : {r_base['wr']:.1f}% → {r_vol['wr']:.1f}% ({r_vol['wr']-r_base['wr']:+.1f}%p)")
    print(f"  PF 변화    : {r_base['pf']:.2f} → {r_vol['pf']:.2f}")
    print(f"  거래 변화  : {len(r_base['trades'])} → {len(r_vol['trades'])} (-{len(r_base['trades'])-len(r_vol['trades'])}건 필터됨)")
    print(f"  MDD 변화   : {r_base['mdd']:.1f}% → {r_vol['mdd']:.1f}%")

    # 필터로 걸러진 거래 중 손실/수익 비율
    base_pnls = sorted(t['pnl'] for t in r_base['trades'])
    vol_pnls = set((t['exit_ts'], t['sym'], t['pnl']) for t in r_vol['trades'])
    filtered_out = [t for t in r_base['trades']
                    if (t['exit_ts'], t['sym'], t['pnl']) not in vol_pnls]
    if filtered_out:
        filt_wins = sum(1 for t in filtered_out if t['pnl'] > 0)
        filt_loss = sum(1 for t in filtered_out if t['pnl'] <= 0)
        filt_pnl = sum(t['pnl'] for t in filtered_out)
        print(f"\n  [볼륨 필터로 걸러진 거래 {len(filtered_out)}건]")
        print(f"    {filt_wins}승 {filt_loss}패 | 합산 PnL: ${filt_pnl:+.2f}")
        if filt_pnl < 0:
            print(f"    → 걸러진 거래들이 순손실 → 필터 효과 입증")


if __name__ == '__main__':
    main()

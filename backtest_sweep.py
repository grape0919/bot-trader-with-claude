"""
파라미터 스윕 — 최적 임계 자동 탐색 (90일 데이터 기반)

탐색 차원:
  1. 볼륨 필터: vol_5/vol_20 임계값 ∈ {none, 1.00, 1.05, 1.10, 1.15, 1.20, 1.25}
  2. 볼륨 윈도우: (5,20), (3,20), (5,30) 비교
  3. ADX 임계: 30 (현재) vs 25
  4. RSI 풀백 진입: (40/60) (현재) vs (45/55)

평가 기준 (스코어):
  pnl × (winrate/50) × min(pf, 3) / max(mdd/30, 1)
  → 수익 + 승률 + PF 보너스, MDD 페널티
  거래 < 20건은 신뢰성 부족으로 스코어 절반 패널티
"""
import time
import numpy as np
import pandas as pd
import ccxt
from itertools import product

import config
from data_cache import load_ohlcv
from strategy import calculate_indicators

SEED = 40.0
DAYS = 90
LEV_T = [(1.0, 60), (2.0, 45), (3.0, 30), (float('inf'), 21)]
LEV_R = [(1.0, 45), (2.0, 30), (3.0, 21), (float('inf'), 15)]
MAX_POS = 3

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
    # 다양한 볼륨 윈도우 미리 계산
    for short, long in [(5, 20), (3, 20), (5, 30)]:
        df[f'vol_{short}_{long}_ratio'] = (
            df['volume'].rolling(short).mean() / df['volume'].rolling(long).mean()
        )
    return df


def check_signal(curr, prev, adx_th, rsi_lo, rsi_hi):
    adx = float(curr['adx']) if not pd.isna(curr['adx']) else 0
    rsi = float(curr['rsi']); prev_rsi = float(prev['rsi'])
    roc5 = float(curr['roc_5']) if not pd.isna(curr['roc_5']) else 0
    vol_r = float(curr['vol_ratio']) if not pd.isna(curr['vol_ratio']) else 0

    if adx > adx_th:
        if curr['ema_fast'] > curr['ema_slow'] > curr['ema_trend']:
            if prev_rsi < rsi_lo and rsi >= rsi_lo: return 'long', 'trend', 1.5, LEV_T
        if curr['ema_fast'] < curr['ema_slow'] < curr['ema_trend']:
            if prev_rsi > rsi_hi and rsi <= rsi_hi: return 'short', 'trend', 1.5, LEV_T
    if adx <= adx_th:
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


def backtest(cache, vol_col, vol_th, adx_th, rsi_lo, rsi_hi):
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
                    trades.append({'sym': sym, 'pnl': pnl, 'md': pos.get('md', '')})
                    del positions[sym]
                continue

            if len(positions) >= MAX_POS: continue
            needed = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
            if any(pd.isna(curr.get(c, np.nan)) for c in needed): continue
            if pd.isna(prev.get('rsi', np.nan)): continue

            sig, md, sl_m, lev_t = check_signal(curr, prev, adx_th, rsi_lo, rsi_hi)
            if not sig: continue

            # 볼륨 필터 (vol_col == None 이면 미적용)
            if vol_col is not None:
                v = curr.get(vol_col, np.nan)
                if pd.isna(v) or float(v) < vol_th:
                    continue

            atr_pct = atr / p * 100
            lev = lev_t[-1][1]
            for th, l2 in lev_t:
                if atr_pct < th: lev = l2; break
            lev = min(lev, 125)
            rem = MAX_POS - len(positions)
            margin = balance / rem if rem > 0 else 0
            if margin < 1: continue
            amt = (margin * lev) / p
            sl = p - atr * sl_m if sig == 'long' else p + atr * sl_m
            positions[sym] = {'s': sig, 'e': p, 'a': amt, 'm': margin, 'sl': sl, 'md': md}
            balance -= margin

        equity = balance + sum(x['m'] for x in positions.values())
        equity_hist.append(equity)

    for sym, pos in list(positions.items()):
        p = float(cache[sym].iloc[min_len-1]['close'])
        diff = (p - pos['e']) if pos['s'] == 'long' else (pos['e'] - p)
        pnl = (diff / pos['e']) * (pos['a'] * pos['e'])
        balance += pos['m'] + pnl
        trades.append({'sym': sym, 'pnl': pnl, 'md': pos.get('md', '')})

    eq = np.array(equity_hist) if equity_hist else np.array([SEED])
    rm = np.maximum.accumulate(eq)
    mdd = float(((rm - eq) / rm * 100).max()) if len(eq) > 1 else 0

    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    pnl_t = balance - SEED
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = (sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses))
          if losses and sum(t['pnl'] for t in losses) != 0 else 0)

    score = pnl_t * (wr / 50.0) * min(pf, 3) / max(mdd / 30.0, 1.0)
    if len(trades) < 20:
        score *= 0.5  # 거래 부족 패널티

    return {'pnl': pnl_t, 'trades': len(trades), 'wins': len(wins),
            'wr': wr, 'pf': pf, 'mdd': mdd, 'score': score, 'bal': balance}


def main():
    print(f'데이터 로드 ({DAYS}일, 10페어)...')
    t0 = time.time()
    cache = {}
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', DAYS)
        cache[sym] = prepare(df)
    print(f'  {time.time()-t0:.1f}초')

    print()
    print('=' * 100)
    print(' 파라미터 스윕 (90일)')
    print('=' * 100)
    print(f"  {'볼륨필터':<14} {'임계':>6} {'ADX':>5} {'RSI풀백':>9} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7} {'점수':>9}")
    print('  ' + '-' * 95)

    vol_configs = [
        (None, 0.0, '없음'),
        ('vol_5_20_ratio', 1.00, '5/20'),
        ('vol_5_20_ratio', 1.05, '5/20'),
        ('vol_5_20_ratio', 1.10, '5/20'),
        ('vol_5_20_ratio', 1.15, '5/20'),
        ('vol_5_20_ratio', 1.20, '5/20'),  # 현재
        ('vol_5_20_ratio', 1.25, '5/20'),
        ('vol_3_20_ratio', 1.10, '3/20'),
        ('vol_3_20_ratio', 1.20, '3/20'),
        ('vol_3_20_ratio', 1.30, '3/20'),
        ('vol_5_30_ratio', 1.10, '5/30'),
        ('vol_5_30_ratio', 1.20, '5/30'),
    ]
    rsi_configs = [(40, 60), (45, 55)]
    adx_configs = [25, 30]

    results = []
    total = len(vol_configs) * len(rsi_configs) * len(adx_configs)
    n = 0
    for (vcol, vth, vlabel) in vol_configs:
        for (rlo, rhi) in rsi_configs:
            for adx_th in adx_configs:
                n += 1
                r = backtest(cache, vcol, vth, adx_th, rlo, rhi)
                tag = f'{vlabel:<6}'
                vth_s = f'{vth:.2f}' if vcol else '—'
                cur = ' (★현재)' if (vcol == 'vol_5_20_ratio' and vth == 1.20 and rlo == 40 and adx_th == 30) else ''
                print(f"  {tag:<14} {vth_s:>6} {adx_th:>5} {f'{rlo}/{rhi}':>9} "
                      f"{r['trades']:>5} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
                      f"${r['pnl']:>+8.2f} {r['mdd']:>5.1f}% {r['score']:>9.1f}{cur}")
                results.append({**r, 'vcol': vcol, 'vth': vth, 'vlabel': vlabel,
                                'rlo': rlo, 'rhi': rhi, 'adx_th': adx_th})

    print()
    print('=' * 100)
    print(' 상위 5개 (스코어 기준)')
    print('=' * 100)
    results.sort(key=lambda x: -x['score'])
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. 볼륨={r['vlabel']}@{r['vth']:.2f}  ADX={r['adx_th']}  RSI={r['rlo']}/{r['rhi']}  "
              f"→ 거래{r['trades']}건  승률{r['wr']:.1f}%  PF{r['pf']:.2f}  "
              f"PnL${r['pnl']:+.2f}  MDD{r['mdd']:.1f}%  점수{r['score']:.1f}")

    print()
    print('=' * 100)
    print(' 수익 상위 5개')
    print('=' * 100)
    results.sort(key=lambda x: -x['pnl'])
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. 볼륨={r['vlabel']}@{r['vth']:.2f}  ADX={r['adx_th']}  RSI={r['rlo']}/{r['rhi']}  "
              f"→ 거래{r['trades']}건  승률{r['wr']:.1f}%  PF{r['pf']:.2f}  "
              f"PnL${r['pnl']:+.2f}  MDD{r['mdd']:.1f}%")


if __name__ == '__main__':
    main()

"""
모멘텀 돌파 진입 추가 효과 검증
─────────────────────────────
가설: 강한 일방향 추세(풀백 없음)에서 RSI 크로스 안 기다리고 진입

추가 시그널 (기존 3가지에 더해):
  - ADX > 30 (강한 추세)
  - |ROC5| > THRESHOLD (강한 모멘텀)
  - EMA9-EMA21 정렬 일치 (방향)
  - 볼륨 필터 통과
  - SL/RSI청산은 trend 모드와 동일

검증:
  1. 기간 안정성 (60/90/120/180일)
  2. ROC5 임계값 sweep (1.0/1.5/2.0/2.5)
  3. 워크포워드 (전반/후반)
  4. 추가된 거래의 PnL 분포 (winner인지 loser인지)
"""
import time
import numpy as np
import pandas as pd
import ccxt

import config
from data_cache import load_ohlcv
from strategy import calculate_indicators

SEED = 40.0
LEV_T = [(1.0, 60), (2.0, 45), (3.0, 30), (float('inf'), 21)]
LEV_R = [(1.0, 45), (2.0, 30), (3.0, 21), (float('inf'), 15)]
MAX_POS = 3
VOL_TH = 1.15

ex = ccxt.bitget({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})


def prepare(df):
    df = calculate_indicators(df)
    df['vol_5'] = df['volume'].rolling(5).mean()
    df['vol_20'] = df['volume'].rolling(20).mean()
    return df


def check_signal(curr, prev, momentum_th=None):
    """
    momentum_th: None=기존 전략만, 숫자=모멘텀 돌파 진입 추가
    """
    adx = float(curr['adx']) if not pd.isna(curr['adx']) else 0
    rsi = float(curr['rsi']); prev_rsi = float(prev['rsi'])
    roc5 = float(curr['roc_5']) if not pd.isna(curr['roc_5']) else 0
    vol_r = float(curr['vol_ratio']) if not pd.isna(curr['vol_ratio']) else 0

    # ─── 기존: 추세 RSI 풀백 ───
    if adx > 30:
        if curr['ema_fast'] > curr['ema_slow'] > curr['ema_trend']:
            if prev_rsi < 40 and rsi >= 40:
                return 'long', 'trend', 1.5, LEV_T
        if curr['ema_fast'] < curr['ema_slow'] < curr['ema_trend']:
            if prev_rsi > 60 and rsi <= 60:
                return 'short', 'trend', 1.5, LEV_T

        # ─── 신규: 모멘텀 돌파 (풀백 없는 강한 추세) ───
        if momentum_th is not None:
            ema_short_bull = curr['ema_fast'] > curr['ema_slow']
            ema_short_bear = curr['ema_fast'] < curr['ema_slow']
            if ema_short_bull and roc5 > momentum_th:
                return 'long', 'momentum', 1.5, LEV_T
            if ema_short_bear and roc5 < -momentum_th:
                return 'short', 'momentum', 1.5, LEV_T

    # ─── 기존: ROC + 횡보 ───
    if adx <= 30:
        if roc5 < -2 and rsi < 35 and vol_r >= 1.3: return 'long', 'roc', 1.2, LEV_R
        if roc5 > 2 and rsi > 65 and vol_r >= 1.3: return 'short', 'roc', 1.2, LEV_R
    if adx <= 25:
        if prev_rsi < 30 and rsi >= 30: return 'long', 'range', 1.5, LEV_R
        if prev_rsi > 70 and rsi <= 70: return 'short', 'range', 1.5, LEV_R
    return None, '', 0, None


def check_exit(rsi, mode, side):
    if mode in ('trend', 'momentum'):
        return (side == 'long' and rsi >= 70) or (side == 'short' and rsi <= 30)
    elif mode == 'roc':
        return (side == 'long' and rsi >= 50) or (side == 'short' and rsi <= 50)
    else:
        return (side == 'long' and rsi >= 55) or (side == 'short' and rsi <= 45)


def backtest(cache, momentum_th, start_idx, end_idx):
    balance = SEED; positions = {}; trades = []; equity_hist = []

    for i in range(start_idx, end_idx):
        for sym in cache:
            df = cache[sym]
            if i >= len(df): continue
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
                    trades.append({'sym': sym, 'pnl': pnl, 'md': pos.get('md', ''),
                                   'side': pos['s'], 'idx': i})
                    del positions[sym]
                continue

            if len(positions) >= MAX_POS: continue
            needed = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
            if any(pd.isna(curr.get(c, np.nan)) for c in needed): continue
            if pd.isna(prev.get('rsi', np.nan)): continue

            sig, md, sl_m, lev_t = check_signal(curr, prev, momentum_th)
            if not sig: continue

            v5m = float(curr.get('vol_5', 0))
            v20m = float(curr.get('vol_20', 0))
            if v20m <= 0 or v5m / v20m < VOL_TH: continue

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
        last_idx = min(end_idx - 1, len(cache[sym]) - 1)
        p = float(cache[sym].iloc[last_idx]['close'])
        diff = (p - pos['e']) if pos['s'] == 'long' else (pos['e'] - p)
        pnl = (diff / pos['e']) * (pos['a'] * pos['e'])
        balance += pos['m'] + pnl
        trades.append({'sym': sym, 'pnl': pnl, 'md': pos.get('md', ''),
                       'side': pos['s'], 'idx': last_idx})

    eq = np.array(equity_hist) if equity_hist else np.array([SEED])
    rm = np.maximum.accumulate(eq)
    mdd = float(((rm - eq) / rm * 100).max()) if len(eq) > 1 else 0
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    pnl_t = balance - SEED
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = (sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses))
          if losses and sum(t['pnl'] for t in losses) != 0 else 0)
    return {'pnl': pnl_t, 'trades': trades, 'n': len(trades), 'wr': wr,
            'pf': pf, 'mdd': mdd}


def main():
    print('데이터 로드 (180일, 10페어)...')
    cache = {}
    for sym in config.SYMBOLS:
        df = load_ohlcv(ex, sym, '15m', 180)
        cache[sym] = prepare(df)
    min_len = min(len(df) for df in cache.values())
    print(f'  공통: {min_len}봉')

    # ─── Test 1: 기간 안정성 ─────────────────────────────
    print('\n' + '=' * 100)
    print(' Test 1. 기간 안정성 (모멘텀 임계 ROC5 = ±1.5%)')
    print('=' * 100)
    print(f"  {'기간':<8} {'전략':<28} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 80)
    for days in [60, 90, 120, 180]:
        candles = days * 96
        end_idx = min_len
        start_idx = max(60, end_idx - candles)
        if start_idx >= end_idx - 50: continue
        r_b = backtest(cache, None, start_idx, end_idx)
        r_m = backtest(cache, 1.5, start_idx, end_idx)
        d = r_m['pnl'] - r_b['pnl']
        sign = '✓' if d > 0 else '×'
        print(f"  {days:>3}일   {'Baseline':<28} {r_b['n']:>5} {r_b['wr']:>6.1f}% "
              f"{r_b['pf']:>5.2f} ${r_b['pnl']:>+8.2f} {r_b['mdd']:>5.1f}%")
        print(f"  {' ':>3}    {'  + 모멘텀돌파(ROC5±1.5)':<28} {r_m['n']:>5} {r_m['wr']:>6.1f}% "
              f"{r_m['pf']:>5.2f} ${r_m['pnl']:>+8.2f} {r_m['mdd']:>5.1f}%  {sign} Δ${d:+.2f}\n")

    # ─── Test 2: ROC5 임계 sweep ─────────────────────────
    print('=' * 100)
    print(' Test 2. ROC5 임계값 sweep (90일)')
    print('=' * 100)
    print(f"  {'전략':<28} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 75)
    end_idx = min_len; start_idx = max(60, end_idx - 90 * 96)
    r_b = backtest(cache, None, start_idx, end_idx)
    print(f"  {'Baseline (모멘텀 없음)':<28} {r_b['n']:>5} {r_b['wr']:>6.1f}% "
          f"{r_b['pf']:>5.2f} ${r_b['pnl']:>+8.2f} {r_b['mdd']:>5.1f}%")
    for th in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]:
        r = backtest(cache, th, start_idx, end_idx)
        d = r['pnl'] - r_b['pnl']
        sign = '✓' if d > 0 else '×'
        print(f"  {f'  + 모멘텀 ROC5 ±{th}':<28} {r['n']:>5} {r['wr']:>6.1f}% "
              f"{r['pf']:>5.2f} ${r['pnl']:>+8.2f} {r['mdd']:>5.1f}%  {sign} Δ${d:+.2f}")

    # ─── Test 3: 워크포워드 ─────────────────────────────
    print()
    print('=' * 100)
    print(' Test 3. 워크포워드 (전반/후반 90일)')
    print('=' * 100)
    mid = min_len // 2
    for name, s, e in [('전반(이전)', 60, mid), ('후반(최근)', mid, min_len)]:
        r_b = backtest(cache, None, s, e)
        r_m = backtest(cache, 1.5, s, e)
        d = r_m['pnl'] - r_b['pnl']
        sign = '✓' if d > 0 else '×'
        print(f"  {name}")
        print(f"    Baseline:        거래{r_b['n']:>3}건 PF{r_b['pf']:.2f} 수익${r_b['pnl']:+.2f} MDD{r_b['mdd']:.1f}%")
        print(f"    + 모멘텀돌파:     거래{r_m['n']:>3}건 PF{r_m['pf']:.2f} 수익${r_m['pnl']:+.2f} MDD{r_m['mdd']:.1f}%  {sign} Δ${d:+.2f}")
        print()

    # ─── Test 4: 추가된 거래만 분석 ───────────────────────
    print('=' * 100)
    print(' Test 4. 모멘텀 모드로 추가된 거래의 실제 PnL (180일, ROC5 ±1.5)')
    print('=' * 100)
    r_m = backtest(cache, 1.5, 60, min_len)
    momentum_trades = [t for t in r_m['trades'] if t['md'] == 'momentum']
    if momentum_trades:
        wins = [t for t in momentum_trades if t['pnl'] > 0]
        losses = [t for t in momentum_trades if t['pnl'] <= 0]
        total = sum(t['pnl'] for t in momentum_trades)
        print(f"  총 모멘텀 거래: {len(momentum_trades)}건")
        print(f"    {len(wins)}승 {len(losses)}패  (승률 {len(wins)/len(momentum_trades)*100:.1f}%)")
        print(f"    합산 PnL: ${total:+.2f}")
        if wins:
            print(f"    평균 수익: ${np.mean([t['pnl'] for t in wins]):+.2f}")
        if losses:
            print(f"    평균 손실: ${np.mean([t['pnl'] for t in losses]):+.2f}")
        # LONG vs SHORT
        long_t = [t for t in momentum_trades if t['side'] == 'long']
        short_t = [t for t in momentum_trades if t['side'] == 'short']
        print(f'\n    LONG: {len(long_t)}건 PnL ${sum(t["pnl"] for t in long_t):+.2f}')
        print(f'    SHORT: {len(short_t)}건 PnL ${sum(t["pnl"] for t in short_t):+.2f}')
    else:
        print('  모멘텀 모드 거래 0건')


if __name__ == '__main__':
    main()

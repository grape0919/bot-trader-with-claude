"""
H7 (ATR 확장 필터) 엄격 검증
─────────────────────────
1. 기간 안정성:    60/90/120/180일 일관된 개선?
2. 임계값 sweep:   ratio 1.0/1.05/1.10/1.15/1.20/1.25/1.30
3. 트레이드 분석:  걸러진 거래의 실제 PnL (Winner를 자른건지 Loser를 자른건지)
4. 워크포워드:     기간 분할(전반/후반)로 일관성 확인
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

exchange = ccxt.bitget({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})


def prepare(df, atr_lookback=5):
    df = calculate_indicators(df)
    df[f'atr_{atr_lookback}ago'] = df['atr'].shift(atr_lookback)
    df['vol_5'] = df['volume'].rolling(5).mean()
    df['vol_20'] = df['volume'].rolling(20).mean()
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


def backtest(cache, atr_filter, atr_ratio, atr_lookback, start_idx, end_idx):
    """기간 [start_idx, end_idx)만 거래"""
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
                                   'idx': i, 'atr_ratio_at_entry': pos.get('atr_ratio', 0)})
                    del positions[sym]
                continue

            if len(positions) >= MAX_POS: continue
            needed = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
            if any(pd.isna(curr.get(c, np.nan)) for c in needed): continue
            if pd.isna(prev.get('rsi', np.nan)): continue

            sig, md, sl_m, lev_t = check_signal(curr, prev)
            if not sig: continue

            # 볼륨 필터
            v5m = float(curr.get('vol_5', 0))
            v20m = float(curr.get('vol_20', 0))
            if v20m <= 0 or v5m / v20m < VOL_TH: continue

            # ATR 확장 필터
            atr_past = curr.get(f'atr_{atr_lookback}ago', np.nan)
            atr_ratio_curr = atr / float(atr_past) if not pd.isna(atr_past) and atr_past > 0 else 0
            if atr_filter and atr_ratio_curr < atr_ratio:
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
            positions[sym] = {'s': sig, 'e': p, 'a': amt, 'm': margin, 'sl': sl,
                              'md': md, 'atr_ratio': atr_ratio_curr}
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
                       'idx': last_idx, 'atr_ratio_at_entry': pos.get('atr_ratio', 0)})

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


def diff_trades(base_trades, filtered_trades):
    """Baseline에는 있고 H7에는 없는 거래 = 필터로 걸러진 거래"""
    bf = {(t['sym'], t['idx']): t for t in base_trades}
    ff = {(t['sym'], t['idx']): t for t in filtered_trades}
    removed = [bf[k] for k in bf if k not in ff]
    return removed


def main():
    print('데이터 로드 (180일, 10페어)...')
    t0 = time.time()
    cache = {}
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', 180)
        cache[sym] = prepare(df)
    print(f'  {time.time()-t0:.1f}초')

    min_len = min(len(df) for df in cache.values())
    print(f'  공통 캔들: {min_len} (~{min_len * 15 / 60 / 24:.0f}일)')
    print()

    # ─── Test 1: 기간 안정성 ─────────────────────────────────────────
    print('=' * 95)
    print(' Test 1. 기간 안정성 — 다른 일수에서도 H7이 개선?')
    print('=' * 95)
    print(f"  {'기간':<8} {'전략':<30} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 80)
    for days in [60, 90, 120, 180]:
        candles = days * 96  # 15m 봉
        end_idx = min_len
        start_idx = max(60, end_idx - candles)
        if start_idx >= end_idx - 50: continue
        r_base = backtest(cache, False, 1.0, 5, start_idx, end_idx)
        r_h7 = backtest(cache, True, 1.1, 5, start_idx, end_idx)
        print(f"  {days:>3}일   {'Baseline':<30} {r_base['n']:>5} "
              f"{r_base['wr']:>6.1f}% {r_base['pf']:>5.2f} ${r_base['pnl']:>+8.2f} {r_base['mdd']:>5.1f}%")
        delta = r_h7['pnl'] - r_base['pnl']
        sign = '✓' if delta > 0 else '×'
        print(f"  {' ':>3}    {'  + H7 ATR×1.1':<30} {r_h7['n']:>5} "
              f"{r_h7['wr']:>6.1f}% {r_h7['pf']:>5.2f} ${r_h7['pnl']:>+8.2f} {r_h7['mdd']:>5.1f}%  "
              f"{sign} Δ${delta:+.2f}")
        print()

    # ─── Test 2: 임계값 sweep ─────────────────────────────────────────
    print('=' * 95)
    print(' Test 2. ATR 임계값 sweep — 1.10이 진짜 최적?')
    print(' (90일 기준)')
    print('=' * 95)
    print(f"  {'전략':<30} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 75)
    end_idx = min_len
    start_idx = max(60, end_idx - 90 * 96)
    r_base = backtest(cache, False, 1.0, 5, start_idx, end_idx)
    print(f"  {'Baseline':<30} {r_base['n']:>5} {r_base['wr']:>6.1f}% "
          f"{r_base['pf']:>5.2f} ${r_base['pnl']:>+8.2f} {r_base['mdd']:>5.1f}%")
    for ratio in [1.00, 1.05, 1.10, 1.15, 1.20, 1.25, 1.30]:
        r = backtest(cache, True, ratio, 5, start_idx, end_idx)
        delta = r['pnl'] - r_base['pnl']
        sign = '✓' if delta > 0 else '×'
        print(f"  {f'  H7 ATR × {ratio:.2f}':<30} {r['n']:>5} {r['wr']:>6.1f}% "
              f"{r['pf']:>5.2f} ${r['pnl']:>+8.2f} {r['mdd']:>5.1f}%  {sign} Δ${delta:+.2f}")

    # ─── Test 3: lookback 변화 (3/5/7봉 전 비교) ──────────────────────
    print()
    print('=' * 95)
    print(' Test 3. lookback 변화 — 5봉 전 비교가 최적?')
    print(' (90일, ratio 1.10 고정)')
    print('=' * 95)
    print(f"  {'전략':<30} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 75)
    print(f"  {'Baseline':<30} {r_base['n']:>5} {r_base['wr']:>6.1f}% "
          f"{r_base['pf']:>5.2f} ${r_base['pnl']:>+8.2f} {r_base['mdd']:>5.1f}%")
    for lb in [3, 5, 7, 10]:
        for sym in config.SYMBOLS:
            cache[sym] = prepare(cache[sym] if False else load_ohlcv(exchange, sym, '15m', 180), lb)
        r = backtest(cache, True, 1.10, lb, start_idx, end_idx)
        delta = r['pnl'] - r_base['pnl']
        sign = '✓' if delta > 0 else '×'
        print(f"  {f'  H7 lookback={lb}봉':<30} {r['n']:>5} {r['wr']:>6.1f}% "
              f"{r['pf']:>5.2f} ${r['pnl']:>+8.2f} {r['mdd']:>5.1f}%  {sign} Δ${delta:+.2f}")

    # ─── Test 4: 워크포워드 (전반 vs 후반) ─────────────────────────────
    # cache 다시 (lookback 5)
    for sym in config.SYMBOLS:
        cache[sym] = prepare(load_ohlcv(exchange, sym, '15m', 180), 5)
    print()
    print('=' * 95)
    print(' Test 4. 워크포워드 — 전반 90일 vs 후반 90일 일관성')
    print('=' * 95)
    mid = min_len // 2
    halves = [('전반(이전 90일)', 60, mid), ('후반(최근 90일)', mid, min_len)]
    for name, s, e in halves:
        r_base = backtest(cache, False, 1.0, 5, s, e)
        r_h7 = backtest(cache, True, 1.10, 5, s, e)
        delta = r_h7['pnl'] - r_base['pnl']
        sign = '✓' if delta > 0 else '×'
        print(f"  {name}")
        print(f"    Baseline: 거래{r_base['n']:>3}건 PF{r_base['pf']:.2f} 수익${r_base['pnl']:+.2f} MDD{r_base['mdd']:.1f}%")
        print(f"    + H7   : 거래{r_h7['n']:>3}건 PF{r_h7['pf']:.2f} 수익${r_h7['pnl']:+.2f} MDD{r_h7['mdd']:.1f}%  {sign} Δ${delta:+.2f}")
        print()

    # ─── Test 5: 걸러진 거래의 실제 결과 ─────────────────────────────
    print('=' * 95)
    print(' Test 5. H7가 걸러낸 거래는 실제로 손해였나?')
    print(' (전체 180일 기준, ratio 1.10)')
    print('=' * 95)
    end_idx = min_len; start_idx = 60
    r_base = backtest(cache, False, 1.0, 5, start_idx, end_idx)
    r_h7 = backtest(cache, True, 1.10, 5, start_idx, end_idx)
    removed = diff_trades(r_base['trades'], r_h7['trades'])
    if removed:
        rem_wins = sum(1 for t in removed if t['pnl'] > 0)
        rem_losses = sum(1 for t in removed if t['pnl'] <= 0)
        rem_pnl = sum(t['pnl'] for t in removed)
        print(f"  걸러진 거래: {len(removed)}건")
        print(f"    {rem_wins}승 {rem_losses}패 (승률 {rem_wins/len(removed)*100:.1f}%)")
        print(f"    합산 PnL: ${rem_pnl:+.2f}")
        if rem_pnl < 0:
            print(f"    → 손실 거래 평균 = 필터 효과 입증")
        else:
            print(f"    → 수익 거래도 잘랐을 가능성 — H7 효과 의심")
        print(f"\n  걸러진 거래 상세 (최대 10건):")
        for t in sorted(removed, key=lambda x: x['idx'])[:10]:
            ar = t.get('atr_ratio_at_entry', 0)
            print(f"    [{t['sym'].split('/')[0]:<5}] mode={t['md']:<6} ATR비={ar:.2f}  "
                  f"PnL=${t['pnl']:+7.2f}")


if __name__ == '__main__':
    main()

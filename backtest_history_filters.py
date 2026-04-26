"""
과거 지표 패턴 필터 효과 검증 (90일)

테스트 대상:
  Baseline: 현재 설정 (볼륨필터만 과거 활용)
  H1: ADX 상승 추세 추가 (adx > adx[-3])
  H2: ADX 평균 강도 추가 (adx[-3:].mean() > 25)
  H3: EMA 정렬 지속 (3봉 연속 정렬)
  H4: EMA 정렬 지속 (5봉 연속 정렬)
  H5: RSI 깊은 풀백 (3봉 중 2봉 이상 oversold/overbought)
  H6: RSI smoothed 크로스 (3봉 평균 RSI 풀백)
  H7: ATR 확장 (atr > atr[-5] × 1.1, 변동성 확장 국면)
  Combo: H1 + H3 (가장 좋은 조합)
"""
import time
import numpy as np
import pandas as pd
import ccxt

import config
from data_cache import load_ohlcv
from strategy import calculate_indicators

SEED = 40.0
DAYS = 90
LEV_T = [(1.0, 60), (2.0, 45), (3.0, 30), (float('inf'), 21)]
LEV_R = [(1.0, 45), (2.0, 30), (3.0, 21), (float('inf'), 15)]
MAX_POS = 3
VOL_TH = 1.15  # 현재 볼륨 임계

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
    # 과거 패턴 컬럼들
    df['adx_3ago'] = df['adx'].shift(3)
    df['adx_avg3'] = df['adx'].rolling(3).mean()
    df['ema_aligned_bull'] = (df['ema_fast'] > df['ema_slow']) & (df['ema_slow'] > df['ema_trend'])
    df['ema_aligned_bear'] = (df['ema_fast'] < df['ema_slow']) & (df['ema_slow'] < df['ema_trend'])
    df['ema_bull_3'] = df['ema_aligned_bull'].rolling(3).sum() == 3
    df['ema_bull_5'] = df['ema_aligned_bull'].rolling(5).sum() == 5
    df['ema_bear_3'] = df['ema_aligned_bear'].rolling(3).sum() == 3
    df['ema_bear_5'] = df['ema_aligned_bear'].rolling(5).sum() == 5
    df['rsi_low3'] = (df['rsi'].rolling(3).apply(lambda x: (x < 40).sum())) >= 2
    df['rsi_high3'] = (df['rsi'].rolling(3).apply(lambda x: (x > 60).sum())) >= 2
    df['rsi_avg3'] = df['rsi'].rolling(3).mean()
    df['atr_expanding'] = df['atr'] > df['atr'].shift(5) * 1.1
    return df


def check_signal(curr, prev, filt):
    """filt: dict with optional flags"""
    adx = float(curr['adx']) if not pd.isna(curr['adx']) else 0
    rsi = float(curr['rsi']); prev_rsi = float(prev['rsi'])
    roc5 = float(curr['roc_5']) if not pd.isna(curr['roc_5']) else 0
    vol_r = float(curr['vol_ratio']) if not pd.isna(curr['vol_ratio']) else 0

    # ─── 추가 필터 ───
    # H1: ADX 상승 추세
    if filt.get('adx_rising'):
        a3 = curr.get('adx_3ago', np.nan)
        if pd.isna(a3) or adx <= float(a3): return None, '', 0, None
    # H2: ADX 3봉 평균 > 25
    if filt.get('adx_avg3'):
        a3 = curr.get('adx_avg3', np.nan)
        if pd.isna(a3) or float(a3) < 25: return None, '', 0, None
    # H7: ATR 확장 국면만
    if filt.get('atr_expanding'):
        if not bool(curr.get('atr_expanding', False)): return None, '', 0, None

    # 추세 RSI 풀백 (ADX > 30)
    if adx > 30:
        ema_bull = curr['ema_fast'] > curr['ema_slow'] > curr['ema_trend']
        ema_bear = curr['ema_fast'] < curr['ema_slow'] < curr['ema_trend']
        # H3/H4: EMA 정렬 N봉 지속
        if filt.get('ema_persist'):
            n = filt['ema_persist']
            col_bull = f'ema_bull_{n}'; col_bear = f'ema_bear_{n}'
            ema_bull = ema_bull and bool(curr.get(col_bull, False))
            ema_bear = ema_bear and bool(curr.get(col_bear, False))
        if ema_bull:
            # H5: RSI 깊은 풀백 (3봉 중 2봉 이상 < 40)
            if filt.get('rsi_deep'):
                if not bool(curr.get('rsi_low3', False)): return None, '', 0, None
            # H6: RSI smoothed 크로스
            if filt.get('rsi_smooth'):
                a = curr.get('rsi_avg3', np.nan); pa = prev.get('rsi_avg3', np.nan)
                if pd.isna(a) or pd.isna(pa) or not (float(pa) < 40 and float(a) >= 40):
                    return None, '', 0, None
            elif prev_rsi < 40 and rsi >= 40:
                return 'long', 'trend', 1.5, LEV_T
            else:
                return None, '', 0, None
            # smoothed/deep 통과 후 일반 진입 (smoothed는 위에서 처리됨)
            if filt.get('rsi_deep') and prev_rsi < 40 and rsi >= 40:
                return 'long', 'trend', 1.5, LEV_T
            if filt.get('rsi_smooth'):
                return 'long', 'trend', 1.5, LEV_T
        if ema_bear:
            if filt.get('rsi_deep'):
                if not bool(curr.get('rsi_high3', False)): return None, '', 0, None
            if filt.get('rsi_smooth'):
                a = curr.get('rsi_avg3', np.nan); pa = prev.get('rsi_avg3', np.nan)
                if pd.isna(a) or pd.isna(pa) or not (float(pa) > 60 and float(a) <= 60):
                    return None, '', 0, None
            elif prev_rsi > 60 and rsi <= 60:
                return 'short', 'trend', 1.5, LEV_T
            else:
                return None, '', 0, None
            if filt.get('rsi_deep') and prev_rsi > 60 and rsi <= 60:
                return 'short', 'trend', 1.5, LEV_T
            if filt.get('rsi_smooth'):
                return 'short', 'trend', 1.5, LEV_T
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


def backtest(cache, filt, label):
    balance = SEED; positions = {}; trades = []; equity_hist = []
    min_len = min(len(df) for df in cache.values())

    for i in range(60, min_len):
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
                    trades.append({'pnl': pnl, 'md': pos.get('md', '')})
                    del positions[sym]
                continue

            if len(positions) >= MAX_POS: continue
            needed = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
            if any(pd.isna(curr.get(c, np.nan)) for c in needed): continue
            if pd.isna(prev.get('rsi', np.nan)): continue

            sig, md, sl_m, lev_t = check_signal(curr, prev, filt)
            if not sig: continue

            # 볼륨 필터 (현재 1.15)
            v5 = curr.get('volume', 0)
            v5m = float(df['volume'].iloc[max(0, i-4):i+1].mean()) if i >= 4 else 0
            v20m = float(df['volume'].iloc[max(0, i-19):i+1].mean()) if i >= 19 else 0
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
        p = float(cache[sym].iloc[min_len-1]['close'])
        diff = (p - pos['e']) if pos['s'] == 'long' else (pos['e'] - p)
        pnl = (diff / pos['e']) * (pos['a'] * pos['e'])
        balance += pos['m'] + pnl
        trades.append({'pnl': pnl, 'md': pos.get('md', '')})

    eq = np.array(equity_hist) if equity_hist else np.array([SEED])
    rm = np.maximum.accumulate(eq)
    mdd = float(((rm - eq) / rm * 100).max()) if len(eq) > 1 else 0
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    pnl_t = balance - SEED
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = (sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses))
          if losses and sum(t['pnl'] for t in losses) != 0 else 0)
    return {'label': label, 'pnl': pnl_t, 'trades': len(trades),
            'wr': wr, 'pf': pf, 'mdd': mdd}


def main():
    print(f'데이터 로드 ({DAYS}일, 10페어)...')
    t0 = time.time()
    cache = {}
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', DAYS)
        cache[sym] = prepare(df)
    print(f'  {time.time()-t0:.1f}초')

    print()
    print('=' * 95)
    print(' 과거 지표 패턴 필터 효과 검증')
    print('=' * 95)
    print(f"  {'필터':<40} {'거래':>5} {'승률':>7} {'PF':>6} {'수익':>10} {'MDD':>7}")
    print('  ' + '-' * 85)

    tests = [
        ({}, 'Baseline (현재 설정)'),
        ({'adx_rising': True}, 'H1: ADX 3봉전 대비 상승'),
        ({'adx_avg3': True}, 'H2: ADX 3봉평균 > 25'),
        ({'ema_persist': 3}, 'H3: EMA 정렬 3봉 연속'),
        ({'ema_persist': 5}, 'H4: EMA 정렬 5봉 연속'),
        ({'rsi_deep': True}, 'H5: RSI 3봉 중 2봉 oversold/over'),
        ({'rsi_smooth': True}, 'H6: RSI 3봉평균 풀백크로스'),
        ({'atr_expanding': True}, 'H7: ATR 확장 국면 (× 1.1)'),
        ({'adx_rising': True, 'ema_persist': 3}, 'Combo: H1 + H3'),
        ({'adx_rising': True, 'ema_persist': 5}, 'Combo: H1 + H4'),
    ]

    results = []
    for filt, label in tests:
        r = backtest(cache, filt, label)
        cur = ' ★' if not filt else ''
        print(f"  {label:<40} {r['trades']:>5} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
              f"${r['pnl']:>+8.2f} {r['mdd']:>5.1f}%{cur}")
        results.append(r)

    print()
    base = results[0]
    print('=' * 95)
    print(' Baseline 대비 개선 정도')
    print('=' * 95)
    for r in results[1:]:
        d_pnl = r['pnl'] - base['pnl']
        d_wr = r['wr'] - base['wr']
        d_pf = r['pf'] - base['pf']
        d_mdd = r['mdd'] - base['mdd']
        d_n = r['trades'] - base['trades']
        verdict = '✓' if d_pnl > 0 and d_mdd <= 0 else ('+' if d_pnl > 0 else '×')
        print(f"  {verdict} {r['label']:<40} "
              f"거래{d_n:+d}  승률{d_wr:+.1f}%p  PF{d_pf:+.2f}  "
              f"수익${d_pnl:+.2f}  MDD{d_mdd:+.1f}%p")


if __name__ == '__main__':
    main()

"""멀티시그널 전략: 추세RSI풀백 + 급락반등ROC + 횡보RSI극단 + ADX필터 + MDD제어"""
import logging
import pandas as pd
import numpy as np
import config

logger = logging.getLogger(__name__)


# ─── 지표 계산 ────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask_plus = plus_dm <= minus_dm
    mask_minus = minus_dm <= plus_dm
    plus_dm[mask_plus] = 0
    minus_dm[mask_minus] = 0
    atr = _atr(high, low, close, length)
    plus_di = 100 * (plus_dm.ewm(span=length, adjust=False).mean() / atr.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=length, adjust=False).mean() / atr.replace(0, np.nan))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(span=length, adjust=False).mean()


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['ema_fast']  = _ema(df['close'], config.EMA_FAST)
    df['ema_slow']  = _ema(df['close'], config.EMA_SLOW)
    df['ema_trend'] = _ema(df['close'], config.EMA_TREND)
    df['rsi']       = _rsi(df['close'], config.RSI_PERIOD)
    df['atr']       = _atr(df['high'], df['low'], df['close'], config.ATR_PERIOD)
    df['adx']       = _adx(df['high'], df['low'], df['close'], config.ADX_PERIOD)
    df['vol_ma']    = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma'].replace(0, np.nan)
    df['roc_5']     = (df['close'] / df['close'].shift(5) - 1) * 100
    # 볼륨 과거 패턴 필터 (최근 5봉 vs 이전 20봉)
    df['vol_5']     = df['volume'].rolling(5).mean()
    df['vol_20']    = df['volume'].rolling(20).mean()
    df['vol_strong'] = df['vol_5'] > df['vol_20'] * config.VOL_STRONG_RATIO
    return df


# ─── 시그널 ───────────────────────────────────────────────────────────────────

def get_signal(df: pd.DataFrame) -> tuple[str | None, str, str]:
    """
    멀티시그널 진입.
    Returns: (signal, mode, reason)
      signal: 'long' | 'short' | None
      mode:   'trend' | 'roc' | 'range' | ''
      reason: 진입 시 'OK', 미진입 시 스킵 사유
    """
    cols = ['ema_fast', 'ema_slow', 'ema_trend', 'rsi', 'atr', 'adx', 'roc_5', 'vol_ratio']
    if df[cols].iloc[-2:].isna().any(axis=None):
        return None, '', '지표 NaN (초기 캔들)'

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    adx      = float(curr['adx'])
    rsi      = float(curr['rsi'])
    prev_rsi = float(prev['rsi'])
    roc5     = float(curr['roc_5'])
    vol_r    = float(curr['vol_ratio'])

    # ── 볼륨 과거 패턴 필터: 최근 5봉 평균 > 이전 20봉 평균 × 1.2 ──
    # 가짜 돌파 걸러냄 (검증: 90일 수익 +$942 → +$1675, MDD 82% → 61%)
    if config.VOL_FILTER_ENABLED:
        if not bool(curr.get('vol_strong', False)):
            vol5 = float(curr.get('vol_5', 0)); vol20 = float(curr.get('vol_20', 0))
            ratio = vol5 / vol20 if vol20 > 0 else 0
            return None, '', f'볼륨 필터 미충족 (vol_5/vol_20={ratio:.2f} < {config.VOL_STRONG_RATIO})'

    # Signal 1: 추세장 RSI 풀백 (ADX > 30)
    if adx > config.ADX_THRESHOLD:
        ema_bull = curr['ema_fast'] > curr['ema_slow'] > curr['ema_trend']
        ema_bear = curr['ema_fast'] < curr['ema_slow'] < curr['ema_trend']

        if ema_bull:
            if prev_rsi < config.RSI_ENTRY_LO and rsi >= config.RSI_ENTRY_LO:
                return 'long', 'trend', 'OK'
            return None, '', f'추세상승 but RSI 풀백크로스 없음 (prev={prev_rsi:.1f} → {rsi:.1f}, 필요: <40→≥40)'
        if ema_bear:
            if prev_rsi > config.RSI_ENTRY_HI and rsi <= config.RSI_ENTRY_HI:
                return 'short', 'trend', 'OK'
            return None, '', f'추세하락 but RSI 풀백크로스 없음 (prev={prev_rsi:.1f} → {rsi:.1f}, 필요: >60→≤60)'
        return None, '', f'추세장(ADX={adx:.1f}) but EMA 정렬 안됨'

    # Signal 2: 급락반등 ROC (ADX ≤ 30)
    if adx <= 30:
        if roc5 < -2 and rsi < 35:
            if vol_r >= 1.3:
                return 'long', 'roc', 'OK'
            return None, '', f'ROC급락+RSI과매도 OK but 거래량부족 (vol_ratio={vol_r:.2f} < 1.3)'
        if roc5 > 2 and rsi > 65:
            if vol_r >= 1.3:
                return 'short', 'roc', 'OK'
            return None, '', f'ROC급등+RSI과매수 OK but 거래량부족 (vol_ratio={vol_r:.2f} < 1.3)'

    # Signal 3: 횡보 RSI 극단 (ADX ≤ 25)
    if adx <= 25:
        if prev_rsi < 30 and rsi >= 30:
            return 'long', 'range', 'OK'
        if prev_rsi > 70 and rsi <= 70:
            return 'short', 'range', 'OK'
        return None, '', f'횡보장(ADX={adx:.1f}) but RSI 극단크로스 없음 (RSI={rsi:.1f})'

    # ADX 25~30 (어중간한 횡보)
    return None, '', f'어중간(ADX={adx:.1f}) RSI={rsi:.1f} ROC={roc5:.2f} vol={vol_r:.2f} — 진입 시그널 없음'


def should_exit(df: pd.DataFrame, side: str, mode: str) -> bool:
    """모드별 청산 조건"""
    if df['rsi'].iloc[-1:].isna().any():
        return False
    rsi = float(df.iloc[-1]['rsi'])

    if mode == 'trend':
        return (side == 'long' and rsi >= config.RSI_EXIT_HI) or \
               (side == 'short' and rsi <= config.RSI_EXIT_LO)
    elif mode == 'roc':
        return (side == 'long' and rsi >= 50) or (side == 'short' and rsi <= 50)
    else:  # range
        return (side == 'long' and rsi >= 55) or (side == 'short' and rsi <= 45)


def get_indicator_snapshot(df: pd.DataFrame) -> dict:
    """현재 지표 스냅샷 (로그용)"""
    curr = df.iloc[-1]
    return {
        'price':     float(curr['close']),
        'rsi':       round(float(curr['rsi']), 1) if not pd.isna(curr['rsi']) else None,
        'adx':       round(float(curr['adx']), 1) if not pd.isna(curr['adx']) else None,
        'atr':       round(float(curr['atr']), 4) if not pd.isna(curr['atr']) else None,
        'atr_pct':   round(float(curr['atr']) / float(curr['close']) * 100, 2) if not pd.isna(curr['atr']) else None,
        'ema9':      round(float(curr['ema_fast']), 2),
        'ema21':     round(float(curr['ema_slow']), 2),
        'ema50':     round(float(curr['ema_trend']), 2),
        'roc5':      round(float(curr['roc_5']), 2) if not pd.isna(curr['roc_5']) else None,
        'vol_ratio': round(float(curr['vol_ratio']), 2) if not pd.isna(curr['vol_ratio']) else None,
    }

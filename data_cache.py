"""
OHLCV 데이터 디스크 캐시
─────────────────────
기존에 저장된 데이터 + 최신 데이터만 새로 받아서 합침.
파켓 파일로 저장 (빠른 I/O + 압축).
"""
import os
import time
import pandas as pd
import ccxt

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_cache')
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_path(symbol: str, timeframe: str) -> str:
    safe = symbol.replace('/', '_').replace(':', '_')
    return os.path.join(CACHE_DIR, f'{safe}_{timeframe}.parquet')


def _fetch_range(exchange, symbol: str, timeframe: str, since_ms: int, end_ms: int) -> pd.DataFrame:
    """since_ms ~ end_ms 구간의 OHLCV 수집"""
    all_data = []
    cur = since_ms
    while cur < end_ms:
        for attempt in range(3):
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=cur, limit=200)
                break
            except ccxt.NetworkError:
                time.sleep(2 * (attempt + 1))
            except ccxt.DDoSProtection:
                time.sleep(5)
        else:
            raise RuntimeError(f'{symbol} {timeframe} 데이터 로드 실패')
        if not ohlcv:
            break
        all_data.extend(ohlcv)
        cur = ohlcv[-1][0] + 1
        if len(ohlcv) < 10:
            break
        time.sleep(0.15)

    if not all_data:
        return pd.DataFrame(columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df = pd.DataFrame(all_data, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    df = df.drop_duplicates(subset='ts').reset_index(drop=True)
    return df


def load_ohlcv(exchange, symbol: str, timeframe: str, days: int = 365) -> pd.DataFrame:
    """
    캐시에서 읽고, 부족한 최신 구간만 추가로 받아서 저장.
    반환: ts 컬럼이 datetime64, 나머지는 float.
    """
    path = _cache_path(symbol, timeframe)
    now_ms = exchange.milliseconds()
    want_since_ms = now_ms - days * 86400 * 1000

    existing = pd.DataFrame(columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    if os.path.exists(path):
        existing = pd.read_parquet(path)
        # ts를 ms 정수 컬럼으로도 사용하기 위해 변환 확인
        if pd.api.types.is_datetime64_any_dtype(existing['ts']):
            existing_ms = (existing['ts'].astype('int64') // 10**6)
        else:
            existing_ms = existing['ts'].astype('int64')
    else:
        existing_ms = pd.Series(dtype='int64')

    # 앞쪽 부족분
    if existing.empty:
        oldest_have = None
        newest_have = None
    else:
        oldest_have = int(existing_ms.min())
        newest_have = int(existing_ms.max())

    new_parts = []

    # 요청한 시작점보다 기존 데이터가 늦게 시작하면 앞부분 채움
    if oldest_have is None or want_since_ms < oldest_have:
        target_end = oldest_have if oldest_have else now_ms
        part = _fetch_range(exchange, symbol, timeframe, want_since_ms, target_end)
        if not part.empty:
            new_parts.append(part)

    # 최신 부족분
    if newest_have is None or newest_have < now_ms - 60_000:
        fetch_from = (newest_have + 1) if newest_have else want_since_ms
        part = _fetch_range(exchange, symbol, timeframe, fetch_from, now_ms)
        if not part.empty:
            new_parts.append(part)

    if new_parts:
        combined_new = pd.concat(new_parts, ignore_index=True)
        if not existing.empty:
            if pd.api.types.is_datetime64_any_dtype(existing['ts']):
                existing_raw = existing.copy()
                existing_raw['ts'] = existing_raw['ts'].astype('int64') // 10**6
                merged = pd.concat([existing_raw, combined_new], ignore_index=True)
            else:
                merged = pd.concat([existing, combined_new], ignore_index=True)
        else:
            merged = combined_new
        merged = merged.drop_duplicates(subset='ts').sort_values('ts').reset_index(drop=True)
        merged.to_parquet(path, index=False)
        df = merged
    else:
        df = existing.copy()
        if pd.api.types.is_datetime64_any_dtype(df['ts']):
            df['ts'] = df['ts'].astype('int64') // 10**6

    # 요청 범위로 컷
    df = df[df['ts'] >= want_since_ms].copy()
    df['ts'] = pd.to_datetime(df['ts'], unit='ms')
    df = df.reset_index(drop=True)
    return df


def load_multi(exchange, symbols: list[str], timeframe: str, days: int = 365) -> dict:
    """여러 심볼 한 번에 로드"""
    out = {}
    for sym in symbols:
        out[sym] = load_ohlcv(exchange, sym, timeframe, days)
    return out


if __name__ == '__main__':
    import config
    exchange = ccxt.bitget({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})
    print('첫 호출 (디스크 저장)...')
    t0 = time.time()
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', 100)
        print(f'  {sym:<20} {len(df)}캔들')
    print(f'소요 {time.time()-t0:.1f}초')
    print()
    print('두번째 호출 (캐시 재사용)...')
    t0 = time.time()
    for sym in config.SYMBOLS:
        df = load_ohlcv(exchange, sym, '15m', 100)
    print(f'소요 {time.time()-t0:.1f}초')

"""Bitget 거래소 연결 및 주문 헬퍼"""
import time
import logging
import ccxt
import config

logger = logging.getLogger(__name__)


def create_exchange() -> ccxt.bitget:
    """ccxt Bitget 인스턴스 생성 (USDT 선물 기본 설정)"""
    exchange = ccxt.bitget({
        'apiKey':   config.API_KEY,
        'secret':   config.API_SECRET,
        'password': config.API_PASSPHRASE,
        'options': {
            'defaultType': 'swap',
        },
        'enableRateLimit': True,
        'timeout': 30000,  # 30초 타임아웃
    })
    return exchange


def _retry(fn, max_attempts=3, label=''):
    """네트워크 오류 시 지수 백오프 재시도"""
    for attempt in range(max_attempts):
        try:
            return fn()
        except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
            if attempt == max_attempts - 1:
                raise
            wait = 2 ** attempt
            logger.warning(f"{label} 네트워크 오류 (시도 {attempt+1}/{max_attempts}), {wait}초 후 재시도: {e}")
            time.sleep(wait)


def get_usdt_balance(exchange: ccxt.bitget) -> float:
    """사용 가능한 USDT 잔고 반환 (재시도 포함)"""
    def _fetch():
        bal = exchange.fetch_balance({'type': 'swap'})
        return float(bal.get('USDT', {}).get('free', 0) or 0)
    return _retry(_fetch, label='[잔고조회]')


def get_open_positions(exchange: ccxt.bitget) -> dict[str, dict]:
    """현재 열린 포지션 {symbol: position_info} (재시도 포함)"""
    def _fetch():
        positions = exchange.fetch_positions()
        return {
            p['symbol']: p
            for p in positions
            if float(p.get('contracts') or 0) > 0
        }
    return _retry(_fetch, label='[포지션조회]')


def setup_symbol(exchange: ccxt.bitget, symbol: str, leverage: int) -> None:
    """마진 모드·레버리지 설정 (포지션 없을 때만 변경 가능)"""
    try:
        exchange.set_margin_mode(config.MARGIN_MODE, symbol)
    except Exception as e:
        logger.debug(f"[{symbol}] margin_mode 설정 (이미 설정됐을 수 있음): {e}")
    try:
        exchange.set_leverage(leverage, symbol)
        logger.debug(f"[{symbol}] 레버리지 {leverage}x 설정")
    except Exception as e:
        logger.warning(f"[{symbol}] 레버리지 설정 실패: {e}")

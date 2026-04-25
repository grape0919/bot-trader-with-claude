"""포지션 사이징 및 리스크 관리"""
import math
import logging
import config

logger = logging.getLogger(__name__)


def get_leverage(atr: float, price: float) -> int:
    """
    ATR% (= ATR / 종가 × 100) 기반 레버리지 자동 결정.
    변동성이 클수록 낮은 레버리지 적용.
    """
    atr_pct = (atr / price) * 100
    for threshold, lev in config.LEV_TIERS:
        if atr_pct < threshold:
            return lev
    return config.LEV_TIERS[-1][1]


def margin_per_trade(available_balance: float, open_count: int) -> float:
    """
    남은 포지션 슬롯에 잔고를 균등 배분.
    예) 잔고 $45, 현재 포지션 1개 → 슬롯 2개 남음 → $22.5씩
    """
    remaining_slots = config.MAX_POSITIONS - open_count
    if remaining_slots <= 0:
        return 0.0
    return available_balance / remaining_slots


def calc_contracts(margin: float, leverage: int, price: float, contract_size: float) -> float:
    """
    계약 수 계산.
    notional = margin × leverage
    contracts = notional / (price × contract_size)
    소수점 3자리 내림 (과잉 주문 방지)
    """
    if price <= 0 or contract_size <= 0:
        return 0.0
    notional  = margin * leverage
    contracts = notional / (price * contract_size)
    return math.floor(contracts * 1000) / 1000


def is_drawdown_exceeded(initial: float, current: float) -> bool:
    """잔고가 초기 자본 대비 MAX_DRAWDOWN 이상 감소하면 True"""
    if initial <= 0:
        return False
    dd = (initial - current) / initial
    if dd >= config.MAX_DRAWDOWN:
        logger.warning(f"[RISK] 낙폭 {dd:.1%} → 최대 낙폭 {config.MAX_DRAWDOWN:.0%} 초과. 봇 정지.")
        return True
    return False

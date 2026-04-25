import os
from dotenv import load_dotenv

load_dotenv()

# ─── API ──────────────────────────────────────────────────────────────────────
API_KEY        = os.getenv('BITGET_API_KEY', '')
API_SECRET     = os.getenv('BITGET_SECRET', '')
API_PASSPHRASE = os.getenv('BITGET_PASSPHRASE', '')

# ─── 거래 대상 페어 (USDT 무기한 선물, 유동성 상위 10개) ────────────────────
SYMBOLS = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'BNB/USDT:USDT',
    'XRP/USDT:USDT',
    'DOGE/USDT:USDT',
    'AVAX/USDT:USDT',
    'LINK/USDT:USDT',
    'ADA/USDT:USDT',
    'NEAR/USDT:USDT',
]

# ─── 전략: 멀티시그널 (추세RSI + 급락반등ROC + 횡보RSI + MDD제어) ────────────
# 3개월 백테스트: $40 → 출금 $213 + 잔고 $50 (ROI +558%)
TIMEFRAME    = '15m'
EMA_FAST     = 9
EMA_SLOW     = 21
EMA_TREND    = 50
RSI_PERIOD   = 14
RSI_ENTRY_LO = 40
RSI_ENTRY_HI = 60
RSI_EXIT_HI  = 70
RSI_EXIT_LO  = 30
ATR_PERIOD   = 14
ADX_PERIOD   = 14
ADX_THRESHOLD = 30
CANDLE_LIMIT = 200

# 볼륨 과거 패턴 필터 (90일 스윕 최적화: 1.20 → 1.15)
# 1.15에서 거래+1, 승률+0.6%p, 수익+$196, MDD동일 (60.7%)
VOL_FILTER_ENABLED = True
VOL_STRONG_RATIO   = 1.15  # vol_5 > vol_20 × 1.15 이면 진입 허용

# ─── 자본 관리: 3배 출금 모델 ────────────────────────────────────────────────
# 잔고가 시드의 3배 도달 시 초과분 자동 출금 → 시드 리셋
# 잔고가 시드의 10% 이하 → 청산 (수동 리로드 필요)
TOTAL_CAPITAL     = 40.0   # 초기 시드
SEED              = 40.0   # 거래 기준금액 (고정)
WITHDRAW_TARGET   = 3.0    # 시드 × 3 도달 시 출금 ($120)
WIPEOUT_THRESHOLD = 0.10   # 시드 × 0.10 이하 → 청산 ($4)

# ─── 리스크 관리 ──────────────────────────────────────────────────────────────
MAX_POSITIONS  = 3
SL_ATR_MULT    = 1.5
TP_ATR_MULT    = 0
TP_SAFETY_MULT = 10.0
MAX_DRAWDOWN   = 0.95   # 출금 모델은 봇이 자체적으로 wipeout 처리
MARGIN_MODE    = 'isolated'

# 3x 공격적 레버리지 (ATR% 기반)
LEV_TIERS = [
    (1.0, 60),
    (2.0, 45),
    (3.0, 30),
    (float('inf'), 21),
]

# ─── 루프 주기 ────────────────────────────────────────────────────────────────
LOOP_INTERVAL = 300

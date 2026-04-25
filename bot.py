"""
실거래 트레이딩 봇 — 멀티시그널 + 3배 출금 모델 + 대시보드

전략:
  Signal 1 (추세): ADX>30 + EMA정렬 + RSI풀백
  Signal 2 (급락반등): ADX≤30 + ROC<-2% + RSI<35 + Vol>1.3
  Signal 3 (횡보): ADX≤25 + RSI 30/70 크로스

자본 관리:
  시드 $40 → 잔고 $120 도달 시 자동 출금 ($80 회수)
  잔고 $4 이하 → 봇 정지 (수동 리로드 필요)
"""
import time
import json
import logging
from datetime import datetime, timedelta

import ccxt
import pandas as pd

import config
from exchange import create_exchange, get_usdt_balance, get_open_positions, setup_symbol
from strategy import calculate_indicators, get_signal, should_exit, get_indicator_snapshot
from risk import calc_contracts

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('trading.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

STATE_FILE = 'live_state.json'

# 모드별 레버리지
LEV_RANGE = [(1.0, 45), (2.0, 30), (3.0, 21), (float('inf'), 15)]


class TradingBot:
    def __init__(self):
        self.exchange = create_exchange()

        # 실제 잔고 기반
        self.seed            = config.SEED
        self.peak_balance    = config.SEED
        self.starting_balance = 0.0   # 봇 첫 실행 시 실제 잔고 (수익 계산용)
        # 출금 모델 추적 (로컬 상태)
        self.total_withdrawn = 0.0
        self.withdrawal_log: list[dict] = []
        self.total_reloads   = 0
        self.running         = False
        # 포지션 모드 추적 (거래소는 trend/roc/range 구분 안 함)
        self.position_modes: dict[str, str] = {}
        self._cycle_indicators: dict = {}

        self._load_state()

    # ─── 상태 저장 ────────────────────────────────────────────────────────────

    def _save_state(self):
        state = {
            'total_withdrawn':  self.total_withdrawn,
            'withdrawal_log':   self.withdrawal_log[-100:],
            'total_reloads':    self.total_reloads,
            'position_modes':   self.position_modes,
            'peak_balance':     self.peak_balance,
            'starting_balance': self.starting_balance,
        }
        try:
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"상태 저장 실패: {e}")

    def _load_state(self):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.total_withdrawn  = state.get('total_withdrawn', 0.0)
            self.withdrawal_log   = state.get('withdrawal_log', [])
            self.total_reloads    = state.get('total_reloads', 0)
            self.position_modes   = state.get('position_modes', {})
            self.peak_balance     = state.get('peak_balance', config.SEED)
            self.starting_balance = state.get('starting_balance', 0.0)
            logger.info(
                f"이전 상태 불러옴 | 누적출금: ${self.total_withdrawn:.2f} | "
                f"청산: {self.total_reloads}회"
            )
        except FileNotFoundError:
            logger.info("저장된 상태 없음 — 새로 시작")
        except Exception as e:
            logger.warning(f"상태 불러오기 실패: {e}")

    # ─── 출금/청산 관리 ──────────────────────────────────────────────────────

    def _check_withdrawal(self, balance: float, positions: dict) -> None:
        """잔고가 시드의 3배 도달 + 포지션 없으면 알림 (실거래는 수동 출금)"""
        if len(positions) > 0:
            return
        target = self.seed * config.WITHDRAW_TARGET
        if balance >= target:
            logger.warning(
                f"[WITHDRAW ALERT] 잔고 ${balance:.2f} ≥ ${target:.2f} (시드 {config.WITHDRAW_TARGET}배) "
                f"→ 거래소에서 ${balance - self.seed:.2f} 수동 출금 권장"
            )

    def _check_wipeout(self, balance: float) -> bool:
        wipe = self.seed * config.WIPEOUT_THRESHOLD
        if balance < wipe:
            logger.error(
                f"[WIPEOUT] 잔고 ${balance:.2f} ≤ ${wipe:.2f} → 봇 정지 "
                f"(수동 리로드 후 재시작 필요)"
            )
            self.total_reloads += 1
            self._save_state()
            return True
        return False

    def _size_multiplier(self, balance: float) -> float:
        if self.peak_balance <= 0:
            return 1.0
        dd = (self.peak_balance - balance) / self.peak_balance
        if dd > 0.15:
            return 0.3
        if dd > 0.10:
            return 0.5
        return 1.0

    # ─── 데이터 ──────────────────────────────────────────────────────────────

    def _fetch_df(self, symbol: str) -> pd.DataFrame:
        for attempt in range(3):
            try:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol, config.TIMEFRAME, limit=config.CANDLE_LIMIT
                )
                df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
                df['ts'] = pd.to_datetime(df['ts'], unit='ms')
                return df
            except ccxt.NetworkError:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                else:
                    raise

    # ─── 주문 실행 ───────────────────────────────────────────────────────────

    def _open_position(self, symbol, side, amount, price, atr, mode, snap) -> bool:
        is_long    = side == 'long'
        order_side = 'buy' if is_long else 'sell'
        sl_mult    = 1.2 if mode == 'roc' else 1.5
        sl_price   = price - atr * sl_mult if is_long else price + atr * sl_mult
        tp_price   = price + atr * config.TP_SAFETY_MULT if is_long else price - atr * config.TP_SAFETY_MULT

        try:
            order_params = {
                'stopLoss': {'triggerPrice': round(sl_price, 6)},
            }
            if config.TP_SAFETY_MULT > 0:
                order_params['takeProfit'] = {'triggerPrice': round(tp_price, 6)}

            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=order_side,
                amount=amount,
                params=order_params,
            )
            self.position_modes[symbol] = mode
            self._save_state()
            logger.info(
                f"[OPEN][{symbol}] {side.upper()} ({mode}) | "
                f"가격:{price:.4f} | 수량:{amount} | "
                f"SL:{sl_price:.4f} | 주문ID:{order.get('id','-')} | "
                f"RSI:{snap['rsi']} ADX:{snap['adx']} ATR%:{snap['atr_pct']} "
                f"ROC5:{snap['roc5']} Vol:{snap['vol_ratio']}"
            )
            return True
        except ccxt.InsufficientFunds as e:
            logger.warning(f"[{symbol}] 잔고 부족: {e}")
        except ccxt.InvalidOrder as e:
            logger.warning(f"[{symbol}] 유효하지 않은 주문: {e}")
        except Exception as e:
            logger.error(f"[{symbol}] 진입 주문 오류: {e}", exc_info=True)
        return False

    def _close_position(self, symbol: str, position: dict, reason: str, snap: dict) -> None:
        side       = position.get('side', '')
        contracts  = float(position.get('contracts') or 0)
        close_side = 'sell' if side == 'long' else 'buy'
        try:
            self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=close_side,
                amount=contracts,
                params={'reduceOnly': True},
            )
            self.position_modes.pop(symbol, None)
            self._save_state()
            logger.info(
                f"[CLOSE][{symbol}] {side.upper()} → {reason} | 수량:{contracts} | "
                f"RSI:{snap['rsi']} ADX:{snap['adx']}"
            )
        except Exception as e:
            logger.error(f"[{symbol}] 청산 실패: {e}", exc_info=True)

    # ─── 사이클 ───────────────────────────────────────────────────────────────

    def _run_cycle(self) -> None:
        balance   = get_usdt_balance(self.exchange)
        positions = get_open_positions(self.exchange)
        self.peak_balance = max(self.peak_balance, balance)
        self._cycle_indicators = {}

        dd = (self.peak_balance - balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0
        tc = min(self.seed, balance)
        logger.info(
            f"── 사이클 | 잔고:${balance:.2f} | 기준:${tc:.2f} | "
            f"누적출금:${self.total_withdrawn:.2f} | 청산:{self.total_reloads}회 | "
            f"포지션:{len(positions)}/{config.MAX_POSITIONS} | DD:{dd:.1f}% ──"
        )

        # 첫 포지션 발견 시 구조 디버그 로그 (한 번만)
        if positions and not hasattr(self, '_pos_logged'):
            for sym, p in positions.items():
                logger.info(
                    f"[DEBUG] 포지션 구조 확인 | symbol={sym} side={p.get('side')} "
                    f"contracts={p.get('contracts')} entryPrice={p.get('entryPrice')} "
                    f"leverage={p.get('leverage')} info_keys={list((p.get('info') or {}).keys())}"
                )
            self._pos_logged = True

        # 출금 알림
        self._check_withdrawal(balance, positions)

        # 청산 체크
        if self._check_wipeout(balance):
            self.running = False
            return

        # 한 사이클당 최대 1개 신규 진입 (안전장치)
        new_entries_this_cycle = 0
        MAX_NEW_ENTRIES = 1

        for symbol in config.SYMBOLS:
            try:
                df   = self._fetch_df(symbol)
                df   = calculate_indicators(df)
                snap = get_indicator_snapshot(df)
                curr = df.iloc[-1]
                price = float(curr['close'])
                atr   = float(curr['atr'])

                # 지표 스냅샷 (대시보드용)
                self._cycle_indicators[symbol] = {
                    'snap': snap, 'signal': '', 'mode': '',
                    'reason': '포지션 보유 중' if symbol in positions else '',
                }

                pos = positions.get(symbol)

                # 기존 포지션 청산 체크
                if pos:
                    mode = self.position_modes.get(symbol, 'trend')
                    side = (pos.get('side', '') or '').lower()
                    if should_exit(df, side, mode):
                        reason = {'trend': 'RSI극단', 'roc': '반등청산', 'range': '평균회귀'}.get(mode, '청산')
                        self._close_position(symbol, pos, reason, snap)
                    continue

                # 최대 포지션
                if len(positions) >= config.MAX_POSITIONS:
                    self._cycle_indicators[symbol]['reason'] = '최대 포지션 도달'
                    continue

                # 한 사이클당 신규 진입 제한 (연속 동일 시그널 방지)
                if new_entries_this_cycle >= MAX_NEW_ENTRIES:
                    self._cycle_indicators[symbol]['reason'] = '사이클 신규진입 한도'
                    continue

                signal, mode, reason = get_signal(df)
                self._cycle_indicators[symbol].update({
                    'signal': signal or '', 'mode': mode, 'reason': reason,
                })

                if not signal:
                    logger.info(
                        f"[SKIP][{symbol}] {reason} | "
                        f"RSI:{snap['rsi']} ADX:{snap['adx']} ATR%:{snap['atr_pct']} "
                        f"ROC5:{snap['roc5']} Vol:{snap['vol_ratio']}"
                    )
                    continue

                # 레버리지
                lev_tiers = config.LEV_TIERS if mode == 'trend' else LEV_RANGE
                atr_pct = atr / price * 100
                leverage = lev_tiers[-1][1]
                for threshold, lev in lev_tiers:
                    if atr_pct < threshold:
                        leverage = lev
                        break

                # 포지션 사이징 (시드 기준 + MDD 제어)
                remaining = config.MAX_POSITIONS - len(positions)
                margin = (tc / remaining) * self._size_multiplier(balance) if remaining > 0 else 0
                if margin < 1.0:
                    continue

                # 안전장치: 실제 가용 잔고 초과 방지
                if margin > balance:
                    logger.warning(
                        f"[{symbol}] 증거금 ${margin:.2f} > 가용잔고 ${balance:.2f} → 스킵"
                    )
                    continue

                market        = self.exchange.market(symbol)
                contract_size = float(market.get('contractSize') or 1)
                min_amount    = float(
                    (market.get('limits') or {}).get('amount', {}).get('min') or 0
                )
                amount = calc_contracts(margin, leverage, price, contract_size)
                if amount <= 0 or amount < min_amount:
                    logger.info(
                        f"[{symbol}] 수량 {amount} < 최소 {min_amount} → 스킵"
                    )
                    continue

                setup_symbol(self.exchange, symbol, leverage)
                success = self._open_position(symbol, signal, amount, price, atr, mode, snap)
                if success:
                    new_entries_this_cycle += 1
                    positions = get_open_positions(self.exchange)

            except ccxt.NetworkError as e:
                logger.warning(f"[{symbol}] 네트워크 오류: {e}")
            except ccxt.ExchangeError as e:
                logger.warning(f"[{symbol}] 거래소 오류: {e}")
            except Exception as e:
                logger.error(f"[{symbol}] 예외: {e}", exc_info=True)

            time.sleep(0.5)

        # 대시보드 갱신
        self._render_dashboard(balance, positions)

    def _render_dashboard(self, balance, positions):
        """실거래용 대시보드 — 실제 잔고 + 포지션"""
        try:
            # 대시보드 포맷에 맞게 state 구성
            pos_dict = {}
            for sym, p in positions.items():
                info = p.get('info') or {}
                entry = (p.get('entryPrice')
                         or info.get('averageOpenPrice')
                         or info.get('openPriceAvg')
                         or 0)
                pos_dict[sym] = {
                    'symbol':      sym,
                    'side':        (p.get('side') or '').lower(),
                    'entry_price': float(entry),
                    'amount':      float(p.get('contracts') or 0),
                    'leverage':    int(p.get('leverage') or 0),
                    'margin':      float(p.get('initialMargin') or info.get('marginSize') or 0),
                    'mode':        self.position_modes.get(sym, ''),
                }

            state = {
                'balance':         balance,
                'initial':         self.starting_balance if self.starting_balance > 0 else self.seed,
                'total_withdrawn': self.total_withdrawn,
                'total_reloads':   self.total_reloads,
                'positions':       pos_dict,
                'trade_log':       [],  # 실거래는 별도 trade_log 관리 안 함
                'withdrawal_log':  self.withdrawal_log,
                'total_trades':    0,
                'win_trades':      0,
            }

            with open('paper_state.json', 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            with open('indicators.json', 'w', encoding='utf-8') as f:
                json.dump(self._cycle_indicators, f, ensure_ascii=False, indent=2)

            from dashboard import update_from_files
            update_from_files()
        except Exception as e:
            logger.warning(f"대시보드 갱신 실패: {e}")

    # ─── 캔들 마감 정렬 대기 ─────────────────────────────────────────────────

    def _sleep_until_next_candle_close(self, offset_seconds: int = 2) -> None:
        """
        다음 15m 캔들 마감(:00, :15, :30, :45) + offset초까지 대기.
        offset_seconds: 거래소 데이터 반영 지연 고려 (기본 2초)
        """
        now = datetime.now()
        mins_past = now.minute % 15
        mins_to_boundary = 15 - mins_past
        next_close = now.replace(second=0, microsecond=0) + timedelta(
            minutes=mins_to_boundary, seconds=offset_seconds
        )
        if next_close <= now:
            next_close += timedelta(minutes=15)
        wait = (next_close - datetime.now()).total_seconds()
        logger.info(
            f"다음 캔들 마감 {next_close.strftime('%H:%M:%S')} 까지 "
            f"{wait:.0f}초 대기"
        )
        time.sleep(max(wait, 1))

    # ─── 진입점 ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.running = True
        balance = get_usdt_balance(self.exchange)
        # 첫 실행 시 시작 잔고 기록 (순수익 계산 기준)
        if self.starting_balance <= 0:
            self.starting_balance = balance
            self._save_state()
            logger.info(f"[초기] 시작 잔고 ${balance:.2f} 기록 → 순수익 계산 기준")
        logger.info(
            f"=== 실거래 봇 시작 === "
            f"잔고:${balance:.2f} | 시드:${self.seed} | 시작기준:${self.starting_balance:.2f} | "
            f"페어:{len(config.SYMBOLS)}개 | TF:{config.TIMEFRAME} | "
            f"전략:멀티시그널(추세+급락반등+횡보)"
        )
        # 최초 실행 시 즉시 한 사이클 돌리고, 이후부터 캔들 마감 정렬
        first_cycle = True
        while self.running:
            try:
                if not first_cycle:
                    self._sleep_until_next_candle_close()
                first_cycle = False
                self._run_cycle()
            except KeyboardInterrupt:
                logger.info("사용자 중단 요청")
                break
            except Exception as e:
                logger.error(f"사이클 오류: {e}", exc_info=True)

        logger.info("=== 실거래 봇 정지 ===")

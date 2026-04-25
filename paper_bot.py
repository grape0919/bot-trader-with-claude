"""
페이퍼 트레이딩 봇 — 멀티시그널 전략
추세RSI풀백 + 급락반등ROC + 횡보RSI극단 + MDD제어
"""
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

import ccxt
import pandas as pd

import config
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

PAPER_STATE_FILE = 'paper_state.json'

# 횡보장 시그널 레버리지
LEV_RANGE = [(1.0, 15), (2.0, 10), (3.0, 7), (float('inf'), 5)]


@dataclass
class VirtualPosition:
    symbol:      str
    side:        str
    entry_price: float
    amount:      float
    leverage:    int
    margin:      float
    sl_price:    float
    tp_price:    float
    mode:        str = ''     # 'trend' | 'roc' | 'range'
    opened_at:   str = field(default_factory=lambda: datetime.now().isoformat())


class PaperTradingBot:
    def __init__(self):
        self.exchange = ccxt.bitget({
            'options': {'defaultType': 'swap'},
            'enableRateLimit': True,
        })
        self.balance         = config.TOTAL_CAPITAL
        self.initial_capital = config.TOTAL_CAPITAL
        self.peak_balance    = config.TOTAL_CAPITAL
        self.positions:  dict[str, VirtualPosition] = {}
        self.trade_log:  list[dict] = []
        self.running     = False
        self.total_trades = 0
        self.win_trades   = 0
        # 출금 모델 추적
        self.total_withdrawn = 0.0
        self.withdrawal_log: list[dict] = []
        self.total_reloads   = 0
        self.reload_log:     list[dict] = []

    # ─── 상태 저장/불러오기 ───────────────────────────────────────────────────

    def save_state(self) -> None:
        state = {
            'balance':         self.balance,
            'initial':         self.initial_capital,
            'peak':            self.peak_balance,
            'positions':       {k: asdict(v) for k, v in self.positions.items()},
            'trade_log':       self.trade_log[-50:],
            'total_trades':    self.total_trades,
            'win_trades':      self.win_trades,
            'total_withdrawn': self.total_withdrawn,
            'withdrawal_log':  self.withdrawal_log[-50:],
            'total_reloads':   self.total_reloads,
            'reload_log':      self.reload_log[-50:],
        }
        with open(PAPER_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load_state(self) -> None:
        try:
            with open(PAPER_STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.balance         = state.get('balance', config.TOTAL_CAPITAL)
            self.initial_capital = state.get('initial', config.TOTAL_CAPITAL)
            self.peak_balance    = state.get('peak', self.balance)
            self.total_trades    = state.get('total_trades', 0)
            self.win_trades      = state.get('win_trades', 0)
            self.total_withdrawn = state.get('total_withdrawn', 0.0)
            self.withdrawal_log  = state.get('withdrawal_log', [])
            self.total_reloads   = state.get('total_reloads', 0)
            self.reload_log      = state.get('reload_log', [])
            for sym, pos_data in state.get('positions', {}).items():
                self.positions[sym] = VirtualPosition(**pos_data)
            self.trade_log = state.get('trade_log', [])
            logger.info(
                f"이전 상태 불러옴 | 잔고: ${self.balance:.2f} | "
                f"포지션: {len(self.positions)}개"
            )
        except FileNotFoundError:
            logger.info("저장된 상태 없음 — 새로 시작")
        except Exception as e:
            logger.warning(f"상태 불러오기 실패: {e}")

    # ─── 자본 관리: 3배 출금 모델 ────────────────────────────────────────────

    def _trading_capital(self) -> float:
        """기준금액은 항상 SEED ($40) 고정. 잔고가 시드보다 작으면 잔고만큼."""
        return min(config.SEED, self.balance)

    def _check_withdrawal(self) -> None:
        """잔고가 시드의 3배 도달 + 포지션 없으면 초과분 자동 출금"""
        if len(self.positions) > 0:
            return
        target = config.SEED * config.WITHDRAW_TARGET
        if self.balance >= target:
            withdraw_amt = self.balance - config.SEED
            self.balance = config.SEED
            self.peak_balance = config.SEED
            self.total_withdrawn += withdraw_amt
            entry = {
                'time':    datetime.now().isoformat(),
                'amount':  round(withdraw_amt, 4),
                'cumulative': round(self.total_withdrawn, 4),
            }
            self.withdrawal_log.append(entry)
            logger.info(
                f"[WITHDRAW] 시드 {config.WITHDRAW_TARGET}배 도달 → "
                f"${withdraw_amt:.2f} 출금 | "
                f"누적 출금: ${self.total_withdrawn:.2f} | "
                f"잔고 리셋: ${self.balance:.2f}"
            )

    def _check_wipeout(self) -> bool:
        """잔고가 시드의 임계값 이하면 청산 처리 후 봇 정지"""
        wipe_threshold = config.SEED * config.WIPEOUT_THRESHOLD
        equity = self.balance + sum(p.margin for p in self.positions.values())
        if equity < wipe_threshold:
            # 모든 포지션 회수 (잔여 마진만)
            for pos in self.positions.values():
                self.balance += pos.margin
            self.positions.clear()
            self.total_reloads += 1
            entry = {
                'time':       datetime.now().isoformat(),
                'wipe_at':    round(equity, 4),
                'cumulative': self.total_reloads,
            }
            self.reload_log.append(entry)
            logger.warning(
                f"[WIPEOUT] 잔고 ${equity:.2f} ≤ ${wipe_threshold:.2f} → "
                f"봇 정지 ({self.total_reloads}회차 청산) | "
                f"수동 리로드 후 재시작 필요"
            )
            return True
        return False

    def _size_multiplier(self) -> float:
        """MDD 제어: 시드 대비 낙폭 시 사이즈 축소"""
        if self.peak_balance <= 0:
            return 1.0
        dd = (self.peak_balance - self.balance) / self.peak_balance
        if dd > 0.15:
            return 0.3
        if dd > 0.10:
            return 0.5
        return 1.0

    # ─── 가상 주문 ────────────────────────────────────────────────────────────

    def _open_virtual(self, symbol, side, amount, price, atr, leverage, margin, mode, snap):
        is_long  = side == 'long'
        sl_mult  = 1.2 if mode == 'roc' else 1.5
        sl_price = price - atr * sl_mult if is_long else price + atr * sl_mult
        tp_price = 0.0
        if config.TP_SAFETY_MULT > 0:
            tp_price = price + atr * config.TP_SAFETY_MULT if is_long else price - atr * config.TP_SAFETY_MULT

        self.positions[symbol] = VirtualPosition(
            symbol=symbol, side=side, entry_price=price,
            amount=amount, leverage=leverage, margin=margin,
            sl_price=sl_price, tp_price=tp_price, mode=mode,
        )
        self.balance -= margin

        exit_label = {'trend': 'RSI>=70/<=30', 'roc': 'RSI>=50/<=50', 'range': 'RSI>=55/<=45'}
        logger.info(
            f"[OPEN][{symbol}] {side.upper()} ({mode}) | "
            f"가격:{price:.4f} | 수량:{amount} | {leverage}x | "
            f"SL:{sl_price:.4f} | 청산:{exit_label.get(mode,'')} | "
            f"증거금:${margin:.2f} | "
            f"RSI:{snap['rsi']} ADX:{snap['adx']} ATR%:{snap['atr_pct']} "
            f"ROC5:{snap['roc5']} Vol:{snap['vol_ratio']} "
            f"EMA:{snap['ema9']}/{snap['ema21']}/{snap['ema50']}"
        )

    def _close_virtual(self, symbol, exit_price, reason, snap):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return

        price_diff = exit_price - pos.entry_price
        if pos.side == 'short':
            price_diff = -price_diff
        pnl = (price_diff / pos.entry_price) * (pos.amount * pos.entry_price)

        self.balance += pos.margin + pnl
        self.peak_balance = max(self.peak_balance, self.balance)
        self.total_trades += 1
        if pnl > 0:
            self.win_trades += 1

        win_rate = (self.win_trades / self.total_trades * 100) if self.total_trades else 0
        pnl_total = self.balance - self.initial_capital

        log_entry = {
            'time':   datetime.now().isoformat(),
            'symbol': symbol, 'side': pos.side, 'mode': pos.mode,
            'entry':  pos.entry_price, 'exit': exit_price,
            'amount': pos.amount, 'pnl': round(pnl, 4), 'reason': reason,
        }
        self.trade_log.append(log_entry)

        sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[CLOSE][{symbol}] {pos.side.upper()} ({pos.mode}) → {reason} | "
            f"진입:{pos.entry_price:.4f} → 청산:{exit_price:.4f} | "
            f"PnL:{sign}{pnl:.4f} USDT | "
            f"잔고:${self.balance:.2f} (총PnL:${pnl_total:+.2f}) | "
            f"승률:{win_rate:.0f}% ({self.win_trades}/{self.total_trades}) | "
            f"RSI:{snap['rsi']} ADX:{snap['adx']}"
        )

    def _check_sl_tp(self, symbol, high, low, snap):
        pos = self.positions.get(symbol)
        if not pos:
            return
        if pos.side == 'long':
            if low <= pos.sl_price:
                self._close_virtual(symbol, pos.sl_price, 'SL', snap)
            elif pos.tp_price > 0 and high >= pos.tp_price:
                self._close_virtual(symbol, pos.tp_price, 'TP', snap)
        else:
            if high >= pos.sl_price:
                self._close_virtual(symbol, pos.sl_price, 'SL', snap)
            elif pos.tp_price > 0 and low <= pos.tp_price:
                self._close_virtual(symbol, pos.tp_price, 'TP', snap)

    # ─── 데이터 로드 ──────────────────────────────────────────────────────────

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

    # ─── 사이클 ───────────────────────────────────────────────────────────────

    def _run_cycle(self) -> None:
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100 if self.peak_balance > 0 else 0
        tc = self._trading_capital()
        logger.info(
            f"── 사이클 | 잔고:${self.balance:.2f} | 기준:${tc:.2f} | "
            f"누적출금:${self.total_withdrawn:.2f} | 청산:{self.total_reloads}회 | "
            f"포지션:{len(self.positions)}/{config.MAX_POSITIONS} | DD:{dd:.1f}% ──"
        )
        # 지표 스냅샷 수집용
        self._cycle_indicators = {}

        # 출금 체크 (포지션 없을 때만)
        self._check_withdrawal()

        # 청산 체크 (시드 10% 이하)
        if self._check_wipeout():
            self.running = False
            return

        for symbol in config.SYMBOLS:
            try:
                df   = self._fetch_df(symbol)
                df   = calculate_indicators(df)
                snap = get_indicator_snapshot(df)
                curr = df.iloc[-1]
                high = float(curr['high'])
                low  = float(curr['low'])

                # 지표 스냅샷 (포지션 보유/미보유 모두 저장)
                self._cycle_indicators[symbol] = {
                    'snap':   snap,
                    'signal': '',
                    'mode':   '',
                    'reason': '포지션 보유 중' if symbol in self.positions else '',
                }

                # SL/TP 체크
                if symbol in self.positions:
                    self._check_sl_tp(symbol, high, low, snap)

                # 청산 시그널
                if symbol in self.positions:
                    pos = self.positions[symbol]
                    if should_exit(df, pos.side, pos.mode):
                        reason = {'trend': 'RSI극단', 'roc': '반등청산', 'range': '평균회귀'}.get(pos.mode, '청산')
                        self._close_virtual(symbol, float(curr['close']), reason, snap)
                    continue

                if len(self.positions) >= config.MAX_POSITIONS:
                    self._cycle_indicators[symbol]['reason'] = '최대 포지션 도달'
                    continue

                signal, mode, reason = get_signal(df)
                # 지표 스냅샷 저장 (대시보드용)
                self._cycle_indicators[symbol] = {
                    'snap':   snap,
                    'signal': signal or '',
                    'mode':   mode,
                    'reason': reason,
                }
                if not signal:
                    logger.info(
                        f"[SKIP][{symbol}] {reason} | "
                        f"가격:{snap['price']:.4f} RSI:{snap['rsi']} ADX:{snap['adx']} "
                        f"ATR%:{snap['atr_pct']} ROC5:{snap['roc5']} Vol:{snap['vol_ratio']} "
                        f"EMA9/21/50:{snap['ema9']}/{snap['ema21']}/{snap['ema50']}"
                    )
                    continue

                price = float(curr['close'])
                atr   = float(curr['atr'])

                # 모드별 레버리지
                if mode == 'trend':
                    lev_tiers = config.LEV_TIERS
                else:
                    lev_tiers = LEV_RANGE

                atr_pct = atr / price * 100
                leverage = lev_tiers[-1][1]
                for threshold, lev in lev_tiers:
                    if atr_pct < threshold:
                        leverage = lev
                        break

                # 2단계 자본관리: 기준금액 기반 포지션 사이징
                tc = self._trading_capital()
                remaining_slots = config.MAX_POSITIONS - len(self.positions)
                margin = (tc / remaining_slots) * self._size_multiplier() if remaining_slots > 0 else 0

                if margin < 1.0:
                    logger.warning(f"[{symbol}] 증거금 ${margin:.2f} 부족")
                    continue

                market        = self.exchange.market(symbol)
                contract_size = float(market.get('contractSize') or 1)
                min_amount    = float(
                    (market.get('limits') or {}).get('amount', {}).get('min') or 0
                )
                amount = calc_contracts(margin, leverage, price, contract_size)
                if amount <= 0 or amount < min_amount:
                    continue

                self._open_virtual(symbol, signal, amount, price, atr, leverage, margin, mode, snap)

            except ccxt.NetworkError as e:
                logger.warning(f"[{symbol}] 네트워크 오류: {e}")
            except Exception as e:
                logger.error(f"[{symbol}] 오류: {e}", exc_info=True)

            time.sleep(0.5)

        self.save_state()
        self._save_indicators()
        self._render_dashboard()

    def _save_indicators(self) -> None:
        """지표 스냅샷 JSON 저장"""
        try:
            with open('indicators.json', 'w', encoding='utf-8') as f:
                json.dump(self._cycle_indicators, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"지표 저장 실패: {e}")

    def _render_dashboard(self) -> None:
        """HTML 대시보드 갱신"""
        try:
            from dashboard import update_from_files
            update_from_files()
        except Exception as e:
            logger.warning(f"대시보드 갱신 실패: {e}")

    # ─── 요약 ─────────────────────────────────────────────────────────────────

    def _print_summary(self) -> None:
        win_rate  = (self.win_trades / self.total_trades * 100) if self.total_trades else 0
        # 순수익 = 잔고 + 누적출금 - 초기시드 - 청산비용(시드×리로드횟수)
        reload_cost = config.SEED * self.total_reloads
        net_profit  = self.balance + self.total_withdrawn - self.initial_capital - reload_cost
        total_invested = self.initial_capital + reload_cost
        roi = net_profit / self.initial_capital * 100
        print()
        print("=" * 60)
        print(" [PAPER] 3배 출금 모델 결과 요약")
        print("=" * 60)
        print(f"  초기 시드      : ${self.initial_capital:.2f}")
        print(f"  현재 잔고      : ${self.balance:.2f}")
        print(f"  누적 출금      : ${self.total_withdrawn:.2f} ({len(self.withdrawal_log)}회)")
        print(f"  청산 횟수      : {self.total_reloads}회 (-${reload_cost:.2f})")
        print(f"  총 투입 자본   : ${total_invested:.2f}")
        print(f"  순수익         : ${net_profit:+.2f} (시드 대비 {roi:+.1f}%)")
        print(f"  거래 수        : {self.total_trades} | 승률: {win_rate:.0f}%")
        if self.withdrawal_log:
            print()
            print("  [출금 내역]")
            for w in self.withdrawal_log[-5:]:
                print(f"    {w['time'][:16]} → ${w['amount']:.2f} (누적 ${w['cumulative']:.2f})")
        if self.trade_log:
            print()
            print("  [최근 거래]")
            for t in self.trade_log[-5:]:
                sign = "+" if t['pnl'] >= 0 else ""
                print(
                    f"    {t['symbol']:<20} {t['side'].upper():<5} ({t.get('mode','')}) "
                    f"{sign}{t['pnl']:.4f} USDT  ({t['reason']})"
                )
        print("=" * 60)

    # ─── 캔들 마감 정렬 대기 ─────────────────────────────────────────────────

    def _sleep_until_next_candle_close(self, offset_seconds: int = 2) -> None:
        """다음 15m 캔들 마감 + offset초까지 대기"""
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
            f"다음 캔들 마감 {next_close.strftime('%H:%M:%S')} 까지 {wait:.0f}초 대기"
        )
        time.sleep(max(wait, 1))

    # ─── 진입점 ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.load_state()
        self.running = True
        logger.info(
            f"=== 페이퍼 트레이딩 시작 === "
            f"자본:${self.balance:.2f} | 페어:{len(config.SYMBOLS)}개 | "
            f"TF:{config.TIMEFRAME} | 전략:멀티시그널(추세+급락반등+횡보)"
        )
        first_cycle = True
        try:
            while self.running:
                if not first_cycle:
                    self._sleep_until_next_candle_close()
                first_cycle = False
                self._run_cycle()
        except KeyboardInterrupt:
            pass

        self._print_summary()
        logger.info("=== 페이퍼 트레이딩 정지 ===")

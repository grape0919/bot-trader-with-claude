"""
Telegram 푸시 알림 — 진입/청산/wipeout/에러 즉시 알림
환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 미설정 시 조용히 무시
"""
import os
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()


def _send_sync(text: str) -> None:
    if not _TOKEN or not _CHAT_ID:
        return
    try:
        import requests
        url = f'https://api.telegram.org/bot{_TOKEN}/sendMessage'
        r = requests.post(url, data={
            'chat_id': _CHAT_ID,
            'text': text,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True,
        }, timeout=5)
        if r.status_code != 200:
            logger.warning(f'Telegram {r.status_code}: {r.text[:200]}')
    except Exception as e:
        logger.warning(f'Telegram 알림 실패: {e}')


def send(text: str, *, blocking: bool = False) -> None:
    """기본 비동기 (트레이딩 루프 차단 안 함)"""
    if blocking:
        _send_sync(text)
    else:
        threading.Thread(target=_send_sync, args=(text,), daemon=True).start()


def entry(symbol: str, side: str, price: float, leverage: int, mode: str, margin: float) -> None:
    arrow = '🟢' if side == 'long' else '🔴'
    send(
        f"{arrow} *진입* `{symbol}`\n"
        f"방향: *{side.upper()}* (mode: {mode})\n"
        f"가격: ${price:,.4f}\n"
        f"증거금: ${margin:.2f} × {leverage}x"
    )


def exit_(symbol: str, side: str, exit_price: float, pnl: float, reason: str) -> None:
    icon = '✅' if pnl > 0 else '❌'
    send(
        f"{icon} *청산* `{symbol}`\n"
        f"방향: {side.upper()} | 사유: {reason}\n"
        f"청산가: ${exit_price:,.4f}\n"
        f"PnL: *${pnl:+.2f}*"
    )


def withdrawal(amount: float, balance_after: float) -> None:
    send(
        f"💰 *출금* ${amount:.2f}\n"
        f"잔여 잔고: ${balance_after:.2f}"
    )


def wipeout(balance: float) -> None:
    send(
        f"🚨 *WIPEOUT* 🚨\n"
        f"잔고 ${balance:.2f} → 봇 정지\n"
        f"수동 자본 재충전 후 재시작 필요"
    )


def error(msg: str) -> None:
    send(f"⚠️ *에러*\n{msg[:500]}")


def startup(balance: float, seed: float) -> None:
    send(
        f"🤖 *봇 시작*\n"
        f"잔고: ${balance:.2f} | 시드: ${seed}"
    )

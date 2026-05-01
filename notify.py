"""
푸시 알림 — 진입/청산/wipeout/에러 즉시 알림
멀티채널 지원: ntfy.sh + Telegram (env에 설정된 것만 활성)

ntfy.sh (권장):
  NTFY_TOPIC=내가-정한-유니크-토픽
  NTFY_SERVER=https://ntfy.sh           (선택, 셀프호스팅 시 변경)

Telegram:
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
"""
import os
import json
import logging
import threading

logger = logging.getLogger(__name__)

# ─── ntfy ──
NTFY_TOPIC = os.environ.get('NTFY_TOPIC', '').strip()
NTFY_SERVER = os.environ.get('NTFY_SERVER', 'https://ntfy.sh').strip().rstrip('/')

# ─── Telegram ──
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()


def _ntfy_send(message: str, title: str = '', priority: int = 3, tags=None) -> None:
    if not NTFY_TOPIC:
        return
    try:
        import requests
        body = {'topic': NTFY_TOPIC, 'message': message}
        if title:
            body['title'] = title
        if priority != 3:
            body['priority'] = priority
        if tags:
            body['tags'] = list(tags)
        r = requests.post(NTFY_SERVER, data=json.dumps(body),
                          headers={'Content-Type': 'application/json'}, timeout=5)
        if r.status_code >= 300:
            logger.warning(f'ntfy {r.status_code}: {r.text[:200]}')
    except Exception as e:
        logger.warning(f'ntfy 실패: {e}')


def _telegram_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        import requests
        r = requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            data={'chat_id': TG_CHAT, 'text': text, 'parse_mode': 'Markdown',
                  'disable_web_page_preview': True},
            timeout=5,
        )
        if r.status_code != 200:
            logger.warning(f'Telegram {r.status_code}: {r.text[:200]}')
    except Exception as e:
        logger.warning(f'Telegram 실패: {e}')


def _dispatch(*, message: str, title: str = '', priority: int = 3, tags=None,
              telegram_text: str = '') -> None:
    """ntfy + telegram 병렬 송신 (블로킹 없음)"""
    if NTFY_TOPIC:
        threading.Thread(target=_ntfy_send,
                         args=(message, title, priority, tags or []),
                         daemon=True).start()
    if TG_TOKEN and TG_CHAT:
        threading.Thread(target=_telegram_send,
                         args=(telegram_text or f'{title}\n{message}',),
                         daemon=True).start()


def send(text: str, *, title: str = '', priority: int = 3, tags=None) -> None:
    """범용 알림"""
    _dispatch(message=text, title=title or '봇 알림', priority=priority, tags=tags,
              telegram_text=f'{title}\n{text}' if title else text)


def entry(symbol: str, side: str, price: float, leverage: int,
          mode: str, margin: float) -> None:
    sym_short = symbol.split('/')[0]
    is_long = side == 'long'
    icon = '🟢' if is_long else '🔴'
    title = f'{icon} {side.upper()} {sym_short} 진입'
    body = (f'mode: {mode}\n'
            f'가격: ${price:,.4f}\n'
            f'증거금: ${margin:.2f} × {leverage}x')
    tags = ['green_circle' if is_long else 'red_circle', 'chart_with_upwards_trend']
    tg = (f"{icon} *진입* `{symbol}`\n"
          f"방향: *{side.upper()}* (mode: {mode})\n"
          f"가격: ${price:,.4f}\n"
          f"증거금: ${margin:.2f} × {leverage}x")
    _dispatch(message=body, title=title, tags=tags, telegram_text=tg)


def exit_(symbol: str, side: str, exit_price: float, pnl: float, reason: str) -> None:
    sym_short = symbol.split('/')[0]
    win = pnl > 0
    icon = '✅' if win else '❌'
    title = f'{icon} {sym_short} 청산  ${pnl:+.2f}'
    body = (f'방향: {side.upper()} | 사유: {reason}\n'
            f'청산가: ${exit_price:,.4f}\n'
            f'PnL: ${pnl:+.2f}')
    tags = ['white_check_mark' if win else 'x', 'moneybag' if win else 'chart_with_downwards_trend']
    tg = (f"{icon} *청산* `{symbol}`\n"
          f"방향: {side.upper()} | 사유: {reason}\n"
          f"청산가: ${exit_price:,.4f}\n"
          f"PnL: *${pnl:+.2f}*")
    _dispatch(message=body, title=title, tags=tags, telegram_text=tg)


def withdrawal(amount: float, balance_after: float) -> None:
    title = f'💰 출금 ${amount:.2f}'
    body = f'잔여 잔고: ${balance_after:.2f}'
    _dispatch(message=body, title=title, tags=['moneybag'],
              telegram_text=f'💰 *출금* ${amount:.2f}\n잔여 잔고: ${balance_after:.2f}')


def wipeout(balance: float) -> None:
    title = '🚨 WIPEOUT — 봇 정지'
    body = (f'잔고 ${balance:.2f} → 봇 자동 정지\n'
            f'수동 자본 재충전 후 재시작 필요')
    _dispatch(message=body, title=title, priority=5,
              tags=['rotating_light', 'warning'],
              telegram_text=f'🚨 *WIPEOUT* 🚨\n잔고 ${balance:.2f} → 봇 정지\n수동 자본 재충전 후 재시작 필요')


def error(msg: str) -> None:
    body = msg[:500]
    _dispatch(message=body, title='⚠️ 봇 에러', priority=4, tags=['warning'],
              telegram_text=f'⚠️ *에러*\n{body}')


def startup(balance: float, seed: float) -> None:
    title = '🤖 봇 시작'
    body = f'잔고: ${balance:.2f} | 시드: ${seed}'
    _dispatch(message=body, title=title, tags=['rocket'],
              telegram_text=f'🤖 *봇 시작*\n잔고: ${balance:.2f} | 시드: ${seed}')

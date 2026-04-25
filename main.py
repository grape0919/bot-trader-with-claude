"""
진입점

실제 거래 : python main.py
페이퍼    : python main.py --paper
"""
import sys
import argparse
from config import API_KEY, API_SECRET, API_PASSPHRASE


def main() -> None:
    parser = argparse.ArgumentParser(description='Bitget 선물 자동 트레이딩 봇')
    parser.add_argument(
        '--paper',
        action='store_true',
        help='페이퍼 트레이딩 모드 (API 키 불필요, 가상 주문)',
    )
    args = parser.parse_args()

    if args.paper:
        # ── 페이퍼 트레이딩 ────────────────────────────────────────────────
        from paper_bot import PaperTradingBot
        print("=" * 50)
        print(" [PAPER] 페이퍼 트레이딩 모드")
        print("=" * 50)
        print(" - 실제 시장 데이터 사용 (Bitget 공개 API)")
        print(" - 주문은 가상 처리 (실제 자금 사용 안 함)")
        print(" - 상태는 paper_state.json 에 자동 저장됨")
        print("=" * 50)
        print()
        bot = PaperTradingBot()
        bot.run()

    else:
        # ── 실제 거래 ──────────────────────────────────────────────────────
        if not all([API_KEY, API_SECRET, API_PASSPHRASE]):
            print("API 키가 설정되지 않았습니다.")
            print(".env 파일을 생성하고 아래 항목을 채워주세요:")
            print()
            print("  BITGET_API_KEY=...")
            print("  BITGET_SECRET=...")
            print("  BITGET_PASSPHRASE=...")
            sys.exit(1)

        from bot import TradingBot
        print("=" * 50)
        print(" Bitget 선물 자동 트레이딩 봇 (실거래)")
        print("=" * 50)
        print()
        print("[경고] 이 봇은 실제 자금으로 자동 거래합니다.")
        print("       선물 거래는 원금 손실 위험이 있습니다.")
        print()
        confirm = input("계속 진행하시겠습니까? (yes 입력): ").strip().lower()
        if confirm != 'yes':
            print("취소되었습니다.")
            sys.exit(0)

        bot = TradingBot()
        try:
            bot.run()
        except KeyboardInterrupt:
            print("\n봇이 중단되었습니다.")


if __name__ == '__main__':
    main()

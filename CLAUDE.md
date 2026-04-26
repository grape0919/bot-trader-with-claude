# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 운영/전략 상세는 [README.md](README.md)를 우선 참고할 것. 이 파일은 Claude 작업 시 주의사항만 정리.

## 프로젝트 개요

Bitget USDT 무기한 선물 자동 트레이딩 봇. Python + ccxt 기반.
- **시드**: $40 USDT 고정 (3배 도달 시 초과분 출금 → 시드 리셋)
- **전략**: 멀티시그널 (추세RSI풀백 + 급락반등ROC + 횡보RSI극단) + 볼륨 필터
- **페어**: 유동성 상위 10개
- **타임프레임**: 15분 (캔들 마감 정렬)
- 90일 백테스트: 시드 $40 → +$2,217 (PF 1.86, 승률 56%, MDD 60.7%)

## 실행 / 운영

```bash
source venv/bin/activate

# 페이퍼
python main.py --paper

# 실거래 (백그라운드)
nohup bash -c 'echo "yes" | python main.py' > /dev/null 2>&1 &

# 종료
pkill -f "python main.py"

# 모니터링
tail -f trading.log
open dashboard.html
```

> ⚠️ stdout을 `trading.log`로 리다이렉트하면 FileHandler와 충돌해 로그 중복. 반드시 `/dev/null`로.

## 백테스트

```bash
python backtest_verify.py    # 현재 설정 90일 검증
python backtest_sweep.py     # 파라미터 자동 탐색 (볼륨/ADX/RSI풀백)
```

## 핵심 파일

| 파일 | 역할 |
|------|------|
| `config.py` | 모든 설정. 변경 시 여기만. |
| `strategy.py` | 지표 + 시그널 (자체구현, pandas-ta 미사용) |
| `bot.py` | 실거래 봇 |
| `paper_bot.py` | 페이퍼 봇 |
| `exchange.py` | Bitget ccxt 연결 + 재시도 |
| `risk.py` | ATR% → 레버리지 |
| `dashboard.py` | HTML 대시보드 |
| `data_cache.py` | OHLCV 파켓 캐시 |
| `backtest_verify.py` | 90일 검증 |
| `backtest_sweep.py` | 파라미터 스윕 |

## 주의사항 / 함정

### Bitget ccxt
- `password` 필드 = passphrase
- `defaultType: 'swap'` 필수
- 마진모드(isolated) → 레버리지 순서로 설정
- swap API 1회 최대 200캔들. 200 요청에 **199** 반환되는 경우 있음 → break 조건 `< 10`
- IP 화이트리스트 미등록 시 40018 에러
- Python 3.14에서 pandas-ta/numba 호환 안 됨

### 폐기된 시도 (재실험 금지)
- 고정 TP, 트레일링 스톱
- RSI 풀백 45/55 (백테스트 -$37)
- ADX 임계 25
- 볼륨 윈도우 3/20, 5/30
- V자 캐처 (12개 지표 모두 실패)
- 다중지표 합의
- 1분/5분 사이클 (15분이 최적)
- 과거 지표 패턴 추가 (ADX 추세, EMA 지속, RSI 평활, ATR 확장 등 모두 실패. 볼륨 5/20만 유효)

상세는 [README.md §6.2](README.md) 참조.

### 파라미터 변경 시
- `config.py` 수정 → `backtest_verify.py`로 효과 확인 → 봇 재시작
- 큰 변경은 `backtest_sweep.py`로 탐색 후

### 봇 재시작 시
- 기존 프로세스 `pgrep -fl "python main.py"` 로 확인 후 종료
- bash 래퍼 PID와 python PID 둘 다 죽여야 함
- `live_state.json` 백업 권장 (특히 `starting_balance` 보존)

## 폴더 정리 정책
- 운영 코드 + 백테스트 검증/스윕 도구만 유지
- 일회성 실험 스크립트는 결과 정리 후 삭제 (히스토리는 README.md §6.2에 보존)

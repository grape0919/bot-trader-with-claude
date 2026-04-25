# Bitget USDT 무기한 선물 자동 트레이딩 봇

Python + ccxt 기반. 시드 $40으로 운영하는 멀티시그널 + 3배 출금 모델.

---

## 1. 빠른 시작

```bash
# 가상환경 + 의존성
source venv/bin/activate
pip install -r requirements.txt

# 페이퍼 트레이딩 (API 키 불필요)
python main.py --paper

# 실거래 (API 키 필요)
cp .env.example .env       # → .env 에 BITGET_API_KEY/SECRET/PASSPHRASE 입력
python main.py             # "yes" 입력 후 시작
```

### 백그라운드 실행 + 로그
```bash
nohup bash -c 'echo "yes" | python main.py' > /dev/null 2>&1 &
# 종료
pkill -f "python main.py"
```

> **주의**: stdout을 `trading.log`로 리다이렉트하면 FileHandler와 충돌해 로그가 중복 기록됨. 반드시 `/dev/null`로.

---

## 2. 모니터링

| 방법 | 명령 | 용도 |
|---|---|---|
| 로그 실시간 | `tail -f trading.log` | 사이클별 시그널/스킵 확인 |
| 대시보드 | `open dashboard.html` | 시각적 잔고/포지션/지표 (자동 새로고침) |
| 프로세스 | `pgrep -fl "main.py"` | 봇 생존 확인 |
| 잔고 상태 | `cat live_state.json` | 누적 출금/청산/시작잔고 |

### 로그에서 확인할 정보

각 사이클 헤더:
```
── 사이클 | 잔고:$44.31 | 기준:$40.00 | 누적출금:$0.00 | 청산:0회 | 포지션:0/3 | DD:0.0% ──
```

스킵 사유 + 지표값:
```
[SKIP][BTC/USDT:USDT] 볼륨 필터 미충족 (vol_5/vol_20=0.93 < 1.15) | RSI:27.1 ADX:46.5 ATR%:0.28 ROC5:-0.56 Vol:0.02
```

진입/청산:
```
[BTC/USDT:USDT] 진입 LONG entry=$X.XX size=$Y.YY lev=21x mode=trend
[BTC/USDT:USDT] 청산 reason=RSI청산 pnl=+$3.42
```

### 거래 내역 분석 (예시 명령)
```bash
# 어제 사이클 수
awk '/^2026-04-24/' trading.log | grep -c "사이클"

# 스킵 사유 집계
awk '/^2026-04-24/' trading.log | grep -oE "\[SKIP\]\[[^]]+\] [^|]+" | \
  sed -E 's|\[SKIP\]\[[^]]+\] ||; s| \(.*||' | sort | uniq -c | sort -rn

# 볼륨 임계 근처(1.10~1.20) 미스 건수
awk '/^2026-04-24/' trading.log | grep "볼륨 필터" | \
  grep -oE "vol_5/vol_20=[0-9.]+" | sed 's/.*=//' | \
  awk '$1>=1.10 && $1<1.20 {n++} END{print n}'
```

---

## 3. 전략 (현재 사용 중)

### 멀티시그널 (3가지) + 볼륨 필터
모든 시그널은 **볼륨 필터를 먼저 통과**해야 함:
- `volume.rolling(5).mean() / volume.rolling(20).mean() > 1.15`
- 가짜 돌파를 걸러냄. 90일 백테스트에서 +$1,180 → +$2,217 (+88%) 효과

**Signal 1 — 추세 RSI 풀백** (ADX > 30)
- LONG: EMA9 > EMA21 > EMA50 + RSI가 40 아래에서 40 상향돌파
- SHORT: EMA9 < EMA21 < EMA50 + RSI가 60 위에서 60 하향돌파

**Signal 2 — 급락반등 ROC** (ADX ≤ 30)
- LONG: ROC5 < -2 + RSI < 35 + 거래량 급증(vol_ratio ≥ 1.3)
- SHORT: ROC5 > +2 + RSI > 65 + 거래량 급증

**Signal 3 — 횡보 RSI 극단** (ADX ≤ 25)
- LONG: RSI가 30 아래에서 30 상향돌파
- SHORT: RSI가 70 위에서 70 하향돌파

### 청산 (우선순위 순)
1. **SL**: ATR × 1.5 (격리 마진, 거래소가 즉시 체결)
2. **모드별 RSI 청산** (주 수익 출구):
   - trend: RSI ≥ 70 (롱) / ≤ 30 (숏)
   - roc:   RSI ≥ 50 / ≤ 50
   - range: RSI ≥ 55 / ≤ 45
3. **안전장치 TP**: ATR × 10 (극단 변동 대비)

### 자본 관리 — 3배 출금 모델
- 시드: **$40 고정**
- 출금: 잔고가 시드 × 3 ($120) 도달 시 초과분 자동 출금 → 시드 리셋
- 청산: 잔고가 시드 × 0.10 ($4) 이하 → 자동 종료(수동 리로드 필요)
- 동시 포지션: 최대 3개

### 레버리지 (ATR% 기반)
| ATR% 구간 | 레버리지 |
|---|---|
| < 1.0% | 60x |
| < 2.0% | 45x |
| < 3.0% | 30x |
| ≥ 3.0% | 21x |

> ⚠️ 3배 출금 모델 + 3x 공격적 레버리지는 의도된 고수익/고리스크 구성. MDD 60.7% 감내 전제.

### 모니터링 주기
- 캔들 마감 정렬: UTC 기준 :00, :15, :30, :45 +2초
- 형성 중 캔들 오신호 회피
- 1분/5분 모니터링도 백테스트 결과 15분 대비 열등 (1m -40.7%, 5m 부분개선, 15m +17.6%)

---

## 4. 파일 구조

### 운영 (active)
| 파일 | 역할 |
|---|---|
| [main.py](main.py) | CLI 진입점 (`--paper` 분기) |
| [bot.py](bot.py) | 실거래 봇 (멀티시그널 + 출금모델 + 대시보드) |
| [paper_bot.py](paper_bot.py) | 페이퍼 봇 (가상 주문, 같은 로직) |
| [strategy.py](strategy.py) | 지표 계산 + 시그널 판정 (자체구현, pandas-ta 미사용) |
| [config.py](config.py) | 모든 설정값 |
| [exchange.py](exchange.py) | Bitget ccxt 연결 (재시도 로직 포함) |
| [risk.py](risk.py) | ATR% → 레버리지 결정 |
| [dashboard.py](dashboard.py) | HTML 대시보드 렌더링 |
| [data_cache.py](data_cache.py) | OHLCV 파켓 캐시 (50배 빠른 백테스트) |

### 백테스트 도구
| 파일 | 용도 |
|---|---|
| [backtest_verify.py](backtest_verify.py) | 현재 설정 90일 검증 (시그널별/심볼별/월별 분석) |
| [backtest_sweep.py](backtest_sweep.py) | 파라미터 스윕 (볼륨 임계 × ADX × RSI풀백 자동 탐색) |

### 런타임 상태/출력
| 파일 | 설명 |
|---|---|
| `live_state.json` | 봇 내부 상태 (출금/청산/시작잔고) |
| `paper_state.json` | 대시보드용 상태 (실거래 봇도 여기 씀) |
| `indicators.json` | 사이클별 지표 스냅샷 |
| `dashboard.html` | 대시보드 (자동 갱신) |
| `trading.log` | 사이클 로그 |
| `data_cache/` | OHLCV 파켓 캐시 |

### 환경
| 파일 | 설명 |
|---|---|
| `.env` | API 키 (gitignore 처리) |
| `.env.example` | 키 템플릿 |
| `requirements.txt` | 의존성 |

---

## 5. 주요 설정 변경 위치

`config.py` 한 곳에서 거의 모든 것 제어:

```python
SYMBOLS              # 거래 페어 (현재 BTC/ETH/SOL/BNB/XRP/DOGE/AVAX/LINK/ADA/NEAR)
TIMEFRAME            # 15m
EMA_FAST/SLOW/TREND  # 9 / 21 / 50
RSI_ENTRY_LO/HI      # 40 / 60 (45/55는 백테스트에서 -$37 손실, 절대 금지)
RSI_EXIT_HI/LO       # 70 / 30
ADX_THRESHOLD        # 30 (25는 가짜 추세 늘어남)
VOL_FILTER_ENABLED   # True
VOL_STRONG_RATIO     # 1.15 (1.10 이하 PF 하락, 1.25 이상 거래 부족)
SL_ATR_MULT          # 1.5
LEV_TIERS            # ATR% 구간별 레버리지
MAX_POSITIONS        # 3
SEED                 # 40.0 (시드 고정)
WITHDRAW_TARGET      # 3.0 → $120 도달 시 출금
WIPEOUT_THRESHOLD    # 0.10 → $4 이하 정지
LOOP_INTERVAL        # 300초 (캔들 정렬로 실제는 15분)
```

---

## 6. 시도했던 전략 / 폐기 이유

### 6.1 채택된 변경 (현재 사용)
| 변경 | 검증 결과 | 비고 |
|---|---|---|
| RSI 풀백 단일 → 멀티시그널(추세+ROC+횡보) | 100일 17건 → 90일 78건 (4.6×) | 빈도 + 수익 모두 개선 |
| 페어 5개 → 10개 | 빈도 약 2배 | BTC/ETH/SOL/BNB/XRP/DOGE/AVAX/LINK/ADA/NEAR |
| 1배 → 3배 출금 모델 + 공격적 레버리지 | 90일 +$2,217 | MDD 60.7% 감수, 시드 $40 유지 |
| 볼륨 필터 (vol_5/vol_20 > 1.20) | +$942 → +$1,675 (+78%) | 가짜 돌파 차단 |
| 볼륨 임계 1.20 → **1.15** | +$2,021 → +$2,217 (+10%) | 90일 스윕 결과 (이번 변경) |
| 캔들 마감 정렬 사이클 (:00,:15,:30,:45 +2초) | 형성중 캔들 오신호 회피 | UTC 기준 |

### 6.2 폐기된 시도 (반드시 다시 시도하지 말 것)

| 시도 | 결과 | 폐기 이유 |
|---|---|---|
| 고정 TP (ATR × 2/3) | RSI 청산 대비 -3% | 시장 상황별 유동적 출구 효과 ↓ |
| 트레일링 스톱 (1 ATR) | -$120 | 진동에 의한 조기 청산 |
| RSI 풀백 임계 45/55 | -$37 손실 (모든 조합) | RSI 진폭 부족, 거의 모든 신호 손절 |
| ADX 임계 25 | PF 1.86 → 1.31 | 가짜 추세 증가 |
| 볼륨 윈도우 3/20, 5/30 | 5/20 대비 모두 열등 | 5봉이 단기 모멘텀 포착에 최적 |
| V자 캐처 (12개 지표 + 다중확정) | 모두 -수익 | V자 감지하면 이미 늦음, 풀백 대비 승률 ↓ |
| V자 + 볼륨 필터 결합 | +$160 (기존 +$729) | 볼륨 필터의 이득은 풀백 시그널에서만 |
| 다중지표 합의 (Consensus) | -PnL | 합의 도달 시점 = 후행 |
| 1분/5분 사이클 | 1m -40.7%, 5m 부분개선만 | 15분이 가장 안정 |
| 반익반본 (반 익절/본전) | 시뮬상 PF 하락 | 수익 캡 이후 추세 손실 |
| 변동성 큰 종목만 거래 | MDD 폭증 | 본 전략은 추세장 풀백 노림, 변동성 ≠ 추세 |
| 단기추세만 → 장기추세 동시고려 | 빈도 -50% | 시그널 자체는 이미 EMA50으로 장기 반영 중 |

---

## 7. Bitget ccxt 특이사항

- `password` 필드에 **passphrase** 입력 (네이밍 주의)
- `defaultType: 'swap'` 필수
- SL은 진입 주문 `params.stopLoss` dict로 전달
- 레버리지 설정 전에 마진 모드 먼저 (isolated → leverage 순서)
- swap API 1회 최대 200개 캔들 → 페이지네이션 필요. 200 요청 시 **199개** 반환되는 케이스 있음 → break 조건 `< 10`
- API 키 IP 화이트리스트 필수 (40018 에러 = IP 미등록)
- Python 3.14에서 pandas-ta/numba 호환 안 됨 → 지표 자체구현 ([strategy.py](strategy.py))

---

## 8. 운영 체크리스트

**일일 점검**
- [ ] `pgrep -fl "main.py"` 봇 살아있나
- [ ] `tail -f trading.log` 사이클 정상 (캔들마다 1회)
- [ ] 대시보드 잔고가 거래소와 일치
- [ ] 스킵 사유 분포에 이상치 없는지 (특정 페어만 매번 에러 등)

**파라미터 재최적화 (월 1회 권장)**
```bash
python backtest_sweep.py     # 90일 데이터로 48조합 스윕 (~3분)
python backtest_verify.py    # 현재 설정 상세 검증
```

**비상 조치**
- IP 변경됨 → Bitget API 키 화이트리스트 갱신
- $4 이하 청산 → `live_state.json` 백업 후 자본 재충전 후 재시작
- 큰 손실 발생 → 백테스트로 시장 regime 변경 여부 확인 → 필요 시 sweep 재실행

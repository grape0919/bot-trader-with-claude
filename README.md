# Bitget USDT 무기한 선물 자동 트레이딩 봇

Python + ccxt 기반. 시드 $40으로 운영하는 멀티시그널 + 3배 출금 모델.

---

## 1. 빠른 시작

### 1.1 로컬 개발 (페이퍼 트레이딩, 백테스트)

```bash
source venv/bin/activate
pip install -r requirements.txt

# 페이퍼
python main.py --paper

# 백테스트
python backtest_verify.py
python backtest_sweep.py
```

### 1.2 서버 배포 (실거래, 권장)

**스택**: Docker Compose + Streamlit + Grafana + SQLite + Telegram 알림 + Tailscale

Ubuntu 서버에서 한 번에:
```bash
git clone <repo> ~/bot && cd ~/bot
./deploy.sh             # Docker, Tailscale 설치 + 컴포즈 빌드/실행
vim .env                # API 키 + (선택) Telegram 토큰 입력
docker compose up -d    # 시작
```

자동으로 기동되는 컨테이너:
- `trading-bot` — 봇 (Bitget API 호출, SQLite/Telegram 로깅)
- `trading-dashboard` — Streamlit 대시보드 (`:8501`)
- `trading-grafana` — Grafana + SQLite 데이터소스 (`:3000`)

> 포트는 `127.0.0.1`에만 바인딩 — Tailscale로만 외부 접근. nginx/SSL 불필요.

---

## 2. 모니터링

| 방법 | 접근 | 용도 |
|---|---|---|
| **Streamlit 대시보드** | `http://<서버>.tailnet.ts.net:8501` | 잔고/거래/지표 실시간 (30초 갱신) |
| **Grafana** | `http://<서버>.tailnet.ts.net:3000` | 시계열 차트, PnL 분포, 드로다운 |
| **Telegram 알림** | 모바일 푸시 | 진입/청산/wipeout 즉시 |
| 봇 로그 | `docker compose logs -f bot` | 사이클별 시그널/스킵 |
| 컨테이너 상태 | `docker compose ps` | 헬스체크 |
| SQLite 직접 쿼리 | `sqlite3 state/trading.db` | 임시 분석 |

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

### 백테스트 / 검증 도구
| 파일 | 용도 | 언제 쓰나 |
|---|---|---|
| [backtest_verify.py](backtest_verify.py) | 현재 설정 90일 검증 (시그널별/심볼별/월별 분석) | 운영 전 한 번, 큰 변경 후 재확인 |
| [backtest_sweep.py](backtest_sweep.py) | 파라미터 스윕 (볼륨 × ADX × RSI풀백 48조합 자동 탐색) | 월 1회 재최적화, 시장 regime 변경 의심 시 |
| [backtest_history_filters.py](backtest_history_filters.py) | 과거 지표 패턴 필터 1차 비교 (ADX/EMA/RSI/ATR 9종) | 새로운 과거 패턴 아이디어 1차 검증 |
| [backtest_h7_validate.py](backtest_h7_validate.py) | 단일 필터 엄격 검증 (기간/임계/lookback/워크포워드/걸러진거래 분석) | 백테스트에서 효과 보인 필터를 적용 전 신뢰성 검증. **curve-fitting 잡기 위한 최종 관문** |

> 💡 **검증 워크플로**: 새 아이디어 → `history_filters`로 1차 스크리닝 → 통과한 것만 `h7_validate` 패턴으로 엄격 검증(이름은 H7 검증용으로 만들었지만 일반화 가능 — 코드 복사해서 다른 필터로 변경) → 모든 테스트 통과해야 적용. H7도 1차에서는 +$342 보였으나 엄격 검증에서 워크포워드 실패로 폐기됨.

### 인프라 (서버 배포)
| 파일 | 역할 |
|---|---|
| [Dockerfile](Dockerfile) | 봇 컨테이너 이미지 (Python 3.12-slim 기반) |
| [Dockerfile.dashboard](Dockerfile.dashboard) | Streamlit 대시보드 이미지 |
| [docker-compose.yml](docker-compose.yml) | 봇 + 대시보드 + Grafana 오케스트레이션 |
| [deploy.sh](deploy.sh) | Ubuntu 서버 부트스트랩 (Docker/Tailscale 설치 + 빌드) |
| [db.py](db.py) | SQLite 시계열 로거 (trades / balance_history / skip_log / events) |
| [notify.py](notify.py) | Telegram 푸시 (entry/exit/wipeout) |
| [dashboard_app.py](dashboard_app.py) | Streamlit 대시보드 앱 |
| [grafana/](grafana/) | 데이터소스 + 대시보드 자동 프로비저닝 JSON |

### 런타임 상태/출력
| 파일/디렉토리 | 설명 |
|---|---|
| `state/live_state.json` | 봇 내부 상태 (출금/청산/시작잔고) |
| `state/paper_state.json` | 대시보드용 상태 |
| `state/indicators.json` | 사이클별 지표 스냅샷 |
| `state/trading.db` | **SQLite — 거래/잔고/스킵 시계열** (Grafana 데이터소스) |
| `logs/trading.log` | 사이클 로그 |
| `data_cache/` | OHLCV 파켓 캐시 |

### 환경
| 파일 | 설명 |
|---|---|
| `.env` | API 키 + Telegram + Grafana (gitignore 처리) |
| `.env.example` | 키 템플릿 |
| `requirements.txt` | 봇 의존성 |
| `requirements-dashboard.txt` | Streamlit 의존성 (별도 컨테이너) |

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
| ADX 상승 추세 필터 (3봉 비교) | -$1,882, MDD +20%p | 후행 신호, 좋은 진입점 잘라냄 |
| ADX 3봉평균 > 25 필터 | -$1,692 (거래 30건) | 너무 제한적, 표본 부족 |
| EMA 정렬 3/5봉 지속 | -$879 | 첫 진입 기회 상실 (지속성 = 후행) |
| RSI 깊은 풀백 (3봉 중 2봉) | -$1,913 | 깨끗한 prev→curr 크로스가 핵심 타이밍 |
| RSI 3봉평균 풀백크로스 | -$1,799 | 평활화 = 타이밍 손실 |
| ATR 확장 필터 (atr > atr[-5] × 1.1) | 90일 +$332, **180일 -$132** | 워크포워드 실패: 전반 Δ-$6/후반 Δ+$332. 효과의 대부분이 DOGE 1건의 -$149 우연 회피. 걸러진 10건 중 6승 4패. Curve-fitting. |

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

**일일 점검 (Docker 기반)**
- [ ] `docker compose ps` 모든 컨테이너 `Up` 상태
- [ ] `docker compose logs --tail 50 bot` 사이클 정상
- [ ] Streamlit 대시보드 잔고가 거래소와 일치
- [ ] Telegram 알림 수신 확인 (마지막 사이클 시각)
- [ ] Grafana 드로다운 차트 이상치 없는지

**컨테이너 관리**
```bash
docker compose ps               # 상태
docker compose logs -f bot      # 봇 로그
docker compose restart bot      # 재시작
docker compose down             # 전체 정지
git pull && docker compose up -d --build   # 코드 업데이트 + 재기동
```

**파라미터 재최적화 (월 1회 권장)**
```bash
python backtest_sweep.py     # 90일 데이터로 48조합 스윕 (~3분)
python backtest_verify.py    # 현재 설정 상세 검증
```

**새 전략/필터 검증 워크플로**
```bash
# 1단계: 1차 스크리닝 (여러 후보 동시 비교)
python backtest_history_filters.py

# 2단계: 통과한 후보를 엄격 검증 (curve-fitting 잡기)
python backtest_h7_validate.py     # H7 검증용 — 다른 필터로 변경하려면 코드 수정
```
검증 게이트 — 모든 항목 통과해야 적용:
- [ ] **기간 안정성**: 60/90/120/180일 모두 개선
- [ ] **임계값 sweep**: 인접 임계값(±0.05)에서도 개선 유지 (좁은 sweet spot 의심)
- [ ] **lookback 안정성**: 인접 lookback에서도 일관된 결과
- [ ] **워크포워드**: 전반/후반 분할에서 모두 개선
- [ ] **걸러진 거래 분석**: 합산 PnL이 명확히 음수, 승률 50% 미만

**비상 조치**
- IP 변경됨 → Bitget API 키 화이트리스트 갱신 (`curl -s https://api.ipify.org`)
- $4 이하 청산 → `state/live_state.json` 백업 후 자본 재충전 후 `docker compose restart bot`
- 큰 손실 발생 → 백테스트로 시장 regime 변경 여부 확인 → 필요 시 sweep 재실행
- Telegram 알림 끊김 → `.env` 토큰 확인, 봇 재시작

---

## 9. 인프라 아키텍처

```
[Bitget API]
     ↑
     │ ccxt (재시도)
     │
┌────┴─────────────────────────────────────────┐
│  Docker Compose (Ubuntu 서버)                │
│                                                │
│  ┌──── trading-bot ────┐                      │
│  │  main.py            │                      │
│  │  └─ strategy        │                      │
│  │  └─ db.log_*        │──┐                   │
│  │  └─ notify.*        │  │                   │
│  └─────────────────────┘  │                   │
│                            │ state/           │
│                            ↓ (volume mount)   │
│  ┌────────── 공유 디렉토리 ───────────┐        │
│  │  state/trading.db (SQLite WAL)    │        │
│  │  state/live_state.json            │        │
│  │  state/indicators.json            │        │
│  └────────────────────────────────────┘        │
│        ↑                ↑                      │
│  (read-only)      (read-only)                  │
│        │                │                      │
│  ┌─ trading-grafana ─┐  ┌─ trading-dashboard ─┐│
│  │ frser-sqlite-ds   │  │ Streamlit + plotly  ││
│  │ :3000             │  │ :8501               ││
│  └───────────────────┘  └─────────────────────┘│
└────────────────────────────────────────────────┘
              ↑                          ↑
              │ Tailscale (외부 포트 X)   │
              ↓                          ↓
         관리자 노트북                Telegram 앱
```

**주요 설계 결정**
- **SQLite 단일 파일**: 별도 DB 서버 불필요, 읽기 전용 마운트로 다중 컨테이너 공유
- **WAL 모드**: 봇이 쓰는 동안 Grafana/Streamlit이 락 없이 읽기
- **Telegram > 이메일/SMS**: 푸시 즉시성 + 무료 + Bot API 단순
- **Tailscale > nginx+SSL**: 인증서 갱신·도메인 불필요, 메시 VPN으로 zero-trust
- **포트 127.0.0.1 바인딩**: 외부 직접 노출 0개 (Tailscale 통해서만 접근)

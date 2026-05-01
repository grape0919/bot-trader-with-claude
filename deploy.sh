#!/usr/bin/env bash
# Ubuntu 서버 부트스트랩 — Docker + Compose + Tailscale 설치 및 봇 배포
# 사용: 서버 SSH 접속 후 ./deploy.sh

set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:grape0919/bot-trader-with-claude.git}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/bot}"

log() { echo -e "\033[1;32m[$(date '+%H:%M:%S')]\033[0m $*"; }

# ─── 1. 시스템 업데이트 + 기본 도구 ──────────────────────────────────
log '시스템 업데이트'
sudo apt-get update -qq
sudo apt-get install -y -qq curl git ca-certificates gnupg

# ─── 2. Docker 설치 (없으면) ─────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    log 'Docker 설치'
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
    log 'Docker 설치 완료. 그룹 적용 위해 재로그인 필요할 수 있음.'
else
    log 'Docker 이미 설치됨 (skip)'
fi

# ─── 3. Tailscale (선택) ─────────────────────────────────────────────
if ! command -v tailscale >/dev/null 2>&1; then
    log 'Tailscale 설치'
    curl -fsSL https://tailscale.com/install.sh | sh
    echo
    echo '>>> 다음 명령으로 Tailscale 연결:'
    echo '    sudo tailscale up'
    echo '    (브라우저 인증 후 머신 이름 확인)'
    echo
else
    log 'Tailscale 이미 설치됨 (skip)'
fi

# ─── 4. 타임존 설정 ───────────────────────────────────────────────────
sudo timedatectl set-timezone Asia/Seoul || true

# ─── 5. 코드 클론 / pull ─────────────────────────────────────────────
if [ -d "$DEPLOY_DIR/.git" ]; then
    log "기존 repo 업데이트: $DEPLOY_DIR"
    git -C "$DEPLOY_DIR" pull
else
    log "Repo 클론: $REPO_URL → $DEPLOY_DIR"
    git clone "$REPO_URL" "$DEPLOY_DIR"
fi

cd "$DEPLOY_DIR"

# ─── 6. .env 템플릿 ──────────────────────────────────────────────────
if [ ! -f .env ]; then
    log '.env 생성 (템플릿)'
    cat > .env <<'EOF'
# ─── Bitget API (필수) ───
BITGET_API_KEY=
BITGET_SECRET=
BITGET_PASSPHRASE=

# ─── 알림 (선택, 여러 채널 동시 사용 가능) ───
# ntfy.sh (권장): 폰 앱 설치 → 토픽 구독 → 같은 값 입력
NTFY_TOPIC=
NTFY_SERVER=https://ntfy.sh

# Telegram (선택)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# ─── Grafana (변경 권장) ───
GRAFANA_USER=admin
GRAFANA_PASSWORD=changeme
EOF
    chmod 600 .env
    log ' .env 파일 생성됨. 키 입력 필요:'
    log "    vim $DEPLOY_DIR/.env"
    exit 0
fi

# ─── 7. 디렉토리 생성 ────────────────────────────────────────────────
mkdir -p state logs data_cache grafana/dashboards grafana/provisioning

# ─── 8. 서버 공인 IP 안내 ────────────────────────────────────────────
PUBLIC_IP=$(curl -s https://api.ipify.org || echo 'unknown')
log "서버 공인 IP: $PUBLIC_IP"
log '👉 Bitget API 키 화이트리스트에 위 IP 추가 필요'

# ─── 9. Docker Compose 빌드 + 시작 ───────────────────────────────────
log 'Docker Compose 빌드 + 시작'
docker compose pull || true
docker compose up -d --build

log '배포 완료. 상태 확인:'
echo '    docker compose ps'
echo '    docker compose logs -f bot'
echo
echo '대시보드 접근 (Tailscale 연결 후):'
echo "    Streamlit: http://<서버명>.tailnet.ts.net:8501"
echo "    Grafana:   http://<서버명>.tailnet.ts.net:3000"

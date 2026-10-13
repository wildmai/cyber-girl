#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! docker compose version >/dev/null 2>&1; then
  echo '需要 Docker Compose v2，请先安装 Docker Compose 插件。' >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo '已创建 .env，请填写两个服务密钥后再次运行 bash deploy/start.sh。'
  exit 1
fi
chmod 600 .env
for model in models/silero-v5/silero_vad.jit models/whisper/base.pt; do
  if [[ ! -s "$model" ]]; then
    echo "缺少模型：$model，请使用包含语音权重的完整部署包。" >&2
    exit 1
  fi
done
if [[ -f compose.ip-https.yaml ]]; then
  bash deploy/ip-https/manage.sh config --quiet
  bash deploy/ip-https/manage.sh up -d --no-build --wait --wait-timeout 120 xiaoman nginx
  bash deploy/ip-https/manage.sh exec -T nginx nginx -t
  bash deploy/ip-https/manage.sh exec -T nginx nginx -s reload
  echo 'HTTPS 服务已启动，访问地址见 compose.ip-https.yaml。'
  exit 0
fi
docker compose config --quiet
if docker image inspect xiaoman:linux-cpu >/dev/null 2>&1; then
  docker compose up -d --no-build
else
  docker compose up -d --build
fi
docker compose ps
echo '服务已启动。通过 SSH 隧道访问 http://127.0.0.1:17860，详细步骤见 LINUX-DEPLOY.md。'

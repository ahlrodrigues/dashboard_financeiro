#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${DASHBOARD_BASE_DIR:-/var/www/html/dashboard_financeiro}"
cd "$BASE_DIR"

LOG_FILE="$BASE_DIR/server.log"
PORT=8000

PIDS=$(pgrep -f "python.*server.py" || true)
if [[ -n "$PIDS" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Parando processos: $PIDS"
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
fi

fuser -k ${PORT}/tcp 2>/dev/null || true
sleep 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando servidor na porta $PORT"
nohup python3 server.py >> "$LOG_FILE" 2>&1 &

sleep 2

if lsof -i :${PORT} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Servidor iniciado com sucesso na porta $PORT"
else
    echo "ERRO: Falha ao iniciar servidor"
    exit 1
fi
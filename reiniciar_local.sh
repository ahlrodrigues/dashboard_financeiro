#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

LOG_FILE="$BASE_DIR/server.log"
PORT=8000

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Verificando processos existentes..."
PIDS=$(pgrep -f "python.*server.py" || true)
if [[ -n "$PIDS" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Parando processos: $PIDS"
    kill $PIDS 2>/dev/null || true
    sleep 2
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando servidor na porta $PORT"
nohup python3 server.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Aguardando servidor iniciar (PID: $NEW_PID)..."
sleep 3

if ps -p $NEW_PID > /dev/null 2>&1; then
    echo "Servidor iniciado com sucesso (PID: $NEW_PID) na porta $PORT"
else
    echo "ERRO: Servidor não está rodando"
    exit 1
fi
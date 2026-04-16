#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${DASHBOARD_BASE_DIR:-/var/www/html/dashboard_financeiro}"
cd "$BASE_DIR"

PID_FILE="$BASE_DIR/server.pid"
LOG_FILE="$BASE_DIR/server.log"
PORT="${DASHBOARD_SERVER_PORT:-8000}"

kill_pid() {
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Parando processo $PID" >> "$LOG_FILE"
            kill "$PID" 2>/dev/null || true
            sleep 1
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
}

kill_pid

PIDS=$(pgrep -f "python.*server.py" || true)
if [[ -n "$PIDS" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Parando processos antigos: $PIDS" >> "$LOG_FILE"
    echo "$PIDS" | xargs kill 2>/dev/null || true
    sleep 1
fi

fuser -k ${PORT}/tcp 2>/dev/null || true
sleep 1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando servidor na porta $PORT" >> "$LOG_FILE"
nohup python3 server.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo $NEW_PID > "$PID_FILE"

sleep 2

if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Servidor iniciado com sucesso (PID: $NEW_PID)" >> "$LOG_FILE"
    echo "Servidor reiniciado com sucesso (PID: $NEW_PID)"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Falha ao iniciar servidor" >> "$LOG_FILE"
    echo "ERRO: Falha ao iniciar servidor"
    exit 1
fi
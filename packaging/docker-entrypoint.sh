#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="http://127.0.0.1:8000"
HEALTH_URL="${BACKEND_URL}/health"
BACKEND_PID=""
ELECTRON_PID=""

shutdown_services() {
	local exit_code=$?
	echo "[entrypoint] Shutting down (exit code ${exit_code})..." >&2

	if [[ -n "${ELECTRON_PID:-}" ]] && kill -0 "${ELECTRON_PID}" 2>/dev/null; then
		kill -TERM "${ELECTRON_PID}" 2>/dev/null || true
		wait "${ELECTRON_PID}" 2>/dev/null || true
	fi

	if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
		kill -TERM "${BACKEND_PID}" 2>/dev/null || true
		wait "${BACKEND_PID}" 2>/dev/null || true
	fi

	exit "${exit_code}"
}

trap shutdown_services SIGINT SIGTERM EXIT

mkdir -p /app/config

# Build the frontend source code
echo "[entrypoint] Building frontend..." >&2
cd /app/frontend
npm run build

# Start FastAPI backend. Host networking lets it bind to 127.0.0.1:8000.
echo "[entrypoint] Starting backend..." >&2
cd /app/backend

uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 1 --loop asyncio &
# uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 1 &
BACKEND_PID=$!

# Wait for backend readiness with a bounded retry loop.
ready=false
for ((i = 0; i < 60; i++)); do
	if curl -fsS "${HEALTH_URL}" >/dev/null 2>&1; then
		ready=true
		break
	fi
	if ! kill -0 "${BACKEND_PID}" 2>/dev/null; then
		echo "[entrypoint] Backend exited before becoming ready." >&2
		exit 1
	fi
	sleep 0.5
done

if [[ "${ready}" != "true" ]]; then
	echo "[entrypoint] Backend did not become ready in time." >&2
	exit 1
fi

echo "[entrypoint] Backend ready at ${BACKEND_URL}" >&2

# Start Electron pointing at the bundled production renderer.
echo "[entrypoint] Starting Electron..." >&2
cd /app/frontend
# Use --no-sandbox because running as an unprivileged container user is the
# expected path on SteamOS; the host launcher is responsible for security.
/app/frontend/node_modules/.bin/electron --no-sandbox . &
ELECTRON_PID=$!

# Keep entrypoint alive until Electron exits, then the trap cleans up backend.
wait "${ELECTRON_PID}" 2>/dev/null || true

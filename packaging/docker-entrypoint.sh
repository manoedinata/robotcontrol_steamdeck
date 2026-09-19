#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="http://127.0.0.1:8000"
HEALTH_URL="${BACKEND_URL}/health"
BACKEND_PID=""
ELECTRON_PID=""

# Exiting is not a negotiation. A polite SIGTERM puts uvicorn into a graceful
# shutdown that waits for open connections to close -- and the renderer's
# control WebSocket and the camera peers are exactly the connections that do
# not close on their own -- so the app appeared to hang on the way out, holding
# the screen for the whole graceful timeout. Nothing here needs to be asked
# twice: the operator has already left.
force_kill() {
	local pid="${1:-}"
	[[ -n "${pid}" ]] || return 0
	kill -0 "${pid}" 2>/dev/null || return 0
	kill -KILL "${pid}" 2>/dev/null || true
}

# Deepest first: killing a parent hands its children to init, where they are no
# longer reachable by parentage.
kill_descendants() {
	local parent="$1" child
	for child in $(pgrep -P "${parent}" 2>/dev/null || true); do
		kill_descendants "${child}"
		kill -KILL "${child}" 2>/dev/null || true
	done
}

shutdown_services() {
	local exit_code=$?
	# Do not run this again for the signal that arrives while it is running.
	trap - SIGINT SIGTERM EXIT
	echo "[entrypoint] Shutting down (exit code ${exit_code})..." >&2

	# ffmpeg and go2rtc are the backend's children, not this script's, so the
	# tree goes before the two processes at the top of it.
	if [[ $$ -eq 1 ]]; then
		# Container init: every process in the PID namespace is ours.
		kill -KILL -1 2>/dev/null || true
	else
		# Run outside a container, only what this script started.
		kill_descendants "$$"
	fi
	force_kill "${ELECTRON_PID:-}"
	force_kill "${BACKEND_PID:-}"

	exit "${exit_code}"
}

trap shutdown_services SIGINT SIGTERM EXIT

mkdir -p /app/config /app/recordings

# Build the frontend source code
echo "[entrypoint] Building frontend..." >&2
cd /app/frontend
npm run build

# Start FastAPI backend. Host networking lets it bind to 127.0.0.1:8000.
echo "[entrypoint] Starting backend..." >&2
cd /app/backend

uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 10 --loop asyncio &
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

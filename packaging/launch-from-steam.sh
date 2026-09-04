#!/usr/bin/env bash
# Launch the Steam Deck Robot Monitor from Steam's non-game library.
# This script starts a single Docker/Podman container that bundles the
# Electron frontend and FastAPI backend, then launches Electron.
#
# Usage:
#   1. Build the image once:
#      ./packaging/build-image.sh
#   2. In Steam, "Add a Non-Steam Game" and point it to this script.
#   3. Launch from Steam Gaming Mode or Desktop Mode.

set -euo pipefail

# Determine the absolute path to the project root (assuming this script is in the root directory)
# PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

IMAGE_NAME="${SDRM_IMAGE:-steamdeck-robot-monitor:latest}"
APP_NAME="${SDRM_APP_NAME:-steamdeck-robot-monitor}"
CONFIG_DIR="${SDRM_CONFIG_DIR:-${HOME}/.config/steamdeck-robot-monitor}"

# Container runtime detection: prefer docker, fall back to podman.
CONTAINER_RUNTIME=""
for runtime in docker podman; do
	if command -v "${runtime}" >/dev/null 2>&1; then
		CONTAINER_RUNTIME="${runtime}"
		break
	fi
done

if [[ -z "${CONTAINER_RUNTIME}" ]]; then
	echo "ERROR: Neither docker nor podman is installed." >&2
	exit 1
fi

mkdir -p "${CONFIG_DIR}"

# -----------------------------------------------------------------------------
# Display / graphics environment discovery
# -----------------------------------------------------------------------------
# SteamOS Desktop Mode and Gaming Mode differ in how the display session is
# exposed. We pass through the most common paths and let Electron fall back
# from Wayland to XWayland if needed.
GRAPHICS_ARGS=()

if [[ -n "${WAYLAND_DISPLAY:-}" ]]; then
	WAYLAND_SOCKET_PATH="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/${WAYLAND_DISPLAY}"
	if [[ -S "${WAYLAND_SOCKET_PATH}" ]]; then
		GRAPHICS_ARGS+=(
			-e "WAYLAND_DISPLAY=${WAYLAND_DISPLAY}"
			-e "XDG_RUNTIME_DIR=/tmp/steamos-runtime"
			-v "${WAYLAND_SOCKET_PATH}:/tmp/steamos-runtime/${WAYLAND_DISPLAY}:ro"
		)
	fi
fi

if [[ -n "${DISPLAY:-}" ]]; then
	GRAPHICS_ARGS+=(-e "DISPLAY=${DISPLAY}")
	# Try to find an X authority file for X11 forwarding.
	XAUTH="${XAUTHORITY:-${HOME}/.Xauthority}"
	if [[ -f "${XAUTH}" ]]; then
		GRAPHICS_ARGS+=(-v "${XAUTH}:/tmp/.Xauthority:ro" -e "XAUTHORITY=/tmp/.Xauthority")
	fi
fi

# If no display variables were discovered, still pass the runtime dir so
# Electron has a chance to connect under gamescope.
if [[ ${#GRAPHICS_ARGS[@]} -eq 0 ]]; then
	echo "WARNING: No WAYLAND_DISPLAY or DISPLAY detected. Passing runtime dir only." >&2
	if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
		GRAPHICS_ARGS+=(-e "XDG_RUNTIME_DIR=/tmp/steamos-runtime")
		GRAPHICS_ARGS+=(-v "${XDG_RUNTIME_DIR}:/tmp/steamos-runtime:ro")
	fi
fi

# -----------------------------------------------------------------------------
# Graphics / input device access
# -----------------------------------------------------------------------------
DEVICE_ARGS=()
if [[ -d /dev/dri ]]; then
	DEVICE_ARGS+=(--device /dev/dri)
fi
# Steam Input / generic controller event devices. These may help Electron see
# gamepads, but they are not always required for the browser Gamepad API.
for dev in /dev/input/js* /dev/input/event*; do
	if [[ -e "${dev}" ]]; then
		DEVICE_ARGS+=(--device "${dev}")
	fi
done

# -----------------------------------------------------------------------------
# Run the container
# -----------------------------------------------------------------------------
echo "[launch] Starting ${APP_NAME} using ${CONTAINER_RUNTIME}..." >&2
"${CONTAINER_RUNTIME}" run \
	--rm \
	--name "${APP_NAME}" \
	--network host \
	--ipc host \
	--cap-add=NET_RAW \
	-e "APP_SETTINGS_DIR=/app/config" \
	-v "${CONFIG_DIR}:/app/config" \
	-v "${PROJECT_ROOT}:/app" \
	-v "${APP_NAME}-node-modules:/app/frontend/node_modules" \
	-v "${APP_NAME}-electron-cache:/opt/electron/cache" \
	-v /run/udev:/run/udev:ro \
	"${GRAPHICS_ARGS[@]}" \
	"${DEVICE_ARGS[@]}" \
	"${IMAGE_NAME}" 2>&1 | tee "launch.log"

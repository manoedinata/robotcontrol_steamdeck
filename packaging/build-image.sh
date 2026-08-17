#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${SDRM_IMAGE:-steamdeck-robot-monitor:latest}"

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

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname -- "${SCRIPT_DIR}")"

cd "${REPO_ROOT}"

echo "[build] Building ${IMAGE_NAME} with ${CONTAINER_RUNTIME}..."
"${CONTAINER_RUNTIME}" build -t "${IMAGE_NAME}" -f "${REPO_ROOT}/Dockerfile" "${REPO_ROOT}"

echo "[build] Done. Run: ${SCRIPT_DIR}/launch-from-steam.sh"

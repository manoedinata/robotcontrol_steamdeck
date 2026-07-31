#!/usr/bin/env bash

set -e

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/electron"

if [[ ! -x "$ELECTRON_BIN" ]]; then
	echo "Electron executable not found: $ELECTRON_BIN" >&2
	echo "Run npm install in $APP_DIR before launching." >&2
	exit 1
fi

if [[ ! -f "$APP_DIR/dist/index.html" ]]; then
	echo "Production build not found: $APP_DIR/dist/index.html" >&2
	echo "Run npm run build in $APP_DIR before launching." >&2
	exit 1
fi

cd "$APP_DIR"
unset LD_PRELOAD
exec "$ELECTRON_BIN" .

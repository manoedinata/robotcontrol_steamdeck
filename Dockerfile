# syntax=docker/dockerfile:1
# Steam Deck Robot Monitor: single container that bundles the Electron UI
# and the FastAPI backend. Run with host networking and a mounted settings
# directory for Steam/Gaming Mode launch.

# -----------------------------------------------------------------------------
# Stage 1: Build the frontend renderer bundle.
# -----------------------------------------------------------------------------
FROM node:22-bookworm AS frontend-builder

WORKDIR /build

# Install dependencies first so package-lock changes reuse layers.
COPY frontend/package.json ./
RUN npm install

# Copy source and build the production renderer into dist/.
COPY frontend/ ./
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 2: Runtime image with backend, Electron, and the built renderer.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    # Use a specific Electron cache directory for predictable installation.
    ELECTRON_CACHE=/opt/electron/cache \
    # The renderer should connect to the backend at the same host.
    VITE_BACKEND_URL=http://127.0.0.1:8000 \
    APP_SETTINGS_DIR=/app/config

# Install runtime dependencies:
#   - Node.js + npm for Electron
#   - X11/Wayland libraries and Mesa for Electron rendering
#   - OpenCV build/runtime dependencies (FFmpeg, codecs)
#   - curl for health checks
#   - tini as a tiny init for signal reaping
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libgtk-3-0 \
    libxtst6 \
    libpci3 \
    libgl1-mesa-dri \
    libglx-mesa0 \
    mesa-utils \
    ffmpeg \
    libsm6 \
    libxext6 \
    libxrender1 \
    tini \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 LTS alongside the system python base image.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install backend Python dependencies.
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy backend source.
COPY backend/ ./backend/
COPY packets-schema.json ./
COPY scripts/ ./scripts/

# Copy the built frontend renderer from the first stage.
COPY --from=frontend-builder /build/dist ./frontend/dist
COPY --from=frontend-builder /build/electron-components ./frontend/electron-components
COPY --from=frontend-builder /build/main.js ./frontend/main.js
COPY --from=frontend-builder /build/index.html ./frontend/index.html
COPY --from=frontend-builder /build/package.json ./frontend/package.json

# Install frontend dependencies (only production runtime files; devDependencies
# include electron which is required at runtime inside the container).
RUN cd /app/frontend && npm install

# Copy the container supervisor entrypoint.
COPY packaging/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Keep the settings directory on a volume by default; Steam launch script will
# bind-mount a host path for persistence.
VOLUME ["/app/config"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/docker-entrypoint.sh"]

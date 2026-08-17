# syntax=docker/dockerfile:1
# Steam Deck Robot Monitor: Base image with all dependencies installed.
# Application source code should be mounted to /app at runtime.

FROM python:3.12-slim-bookworm

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

# -----------------------------------------------------------------------------
# Install Dependencies (Cached Layer)
# -----------------------------------------------------------------------------

# 1. Python Backend Dependencies
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# 2. Node.js Frontend Dependencies
COPY frontend/package.json ./frontend/
# Note: Uncomment the line below if you use package-lock.json (recommended for consistent installs)
# COPY frontend/package-lock.json ./frontend/
RUN cd frontend && npm install

# -----------------------------------------------------------------------------
# Runtime Setup
# -----------------------------------------------------------------------------

# Copy the container supervisor entrypoint.
COPY packaging/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Keep the settings directory on a volume by default.
VOLUME ["/app/config"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/docker-entrypoint.sh"]

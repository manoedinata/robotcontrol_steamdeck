# syntax=docker/dockerfile:1
# Steam Deck Robot Monitor: Base image with all dependencies installed.
# Application source code should be mounted to /app at runtime.

FROM python:3.12-slim-bookworm

ARG GO2RTC_VERSION=1.9.14
ARG TARGETARCH
ARG GO2RTC_SHA256_AMD64=32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6
ARG GO2RTC_SHA256_ARM64=359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    # Use a specific Electron cache directory for predictable installation.
    ELECTRON_CACHE=/opt/electron/cache \
    # The renderer should connect to the backend at the same host.
    VITE_BACKEND_URL=http://127.0.0.1:8000 \
    APP_SETTINGS_DIR=/app/config \
    RECORDINGS_DIR=/app/recordings

# Change APT mirror to kartolo.sby.datautama.net.id
RUN sed -i 's|http://deb.debian.org/debian|https://kartolo.sby.datautama.net.id/debian|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org/debian|https://kartolo.sby.datautama.net.id/debian|g' /etc/apt/sources.list

# Prevent Debian from automatically cleaning up the apt cache
RUN rm -f /etc/apt/apt.conf.d/docker-clean && \
    echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

# Install runtime dependencies using BuildKit cache mounts
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt/archives \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    iputils-ping \
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
    tini

# go2rtc is a single static binary. Docker's TARGETARCH selects the matching
# Steam Deck/container architecture while GO2RTC_VERSION keeps upgrades explicit.
RUN case "${TARGETARCH:-amd64}" in \
    amd64) GO2RTC_ARCH=amd64; GO2RTC_SHA256="${GO2RTC_SHA256_AMD64}" ;; \
    arm64) GO2RTC_ARCH=arm64; GO2RTC_SHA256="${GO2RTC_SHA256_ARM64}" ;; \
    *) echo "Unsupported go2rtc architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && curl -fL --retry 3 \
    "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${GO2RTC_ARCH}" \
    -o /usr/local/bin/go2rtc \
    && echo "${GO2RTC_SHA256}  /usr/local/bin/go2rtc" | sha256sum -c - \
    && chmod 0755 /usr/local/bin/go2rtc \
    && /usr/local/bin/go2rtc --version

# Install Node.js 22 LTS alongside the system python base image, utilizing the apt cache.
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache/apt/archives \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs

WORKDIR /app

# -----------------------------------------------------------------------------
# Install Dependencies (Cached Layer)
# -----------------------------------------------------------------------------

# 1. Python Backend Dependencies
COPY backend/requirements.txt ./backend/
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r backend/requirements.txt

# 2. Node.js Frontend Dependencies
COPY frontend/package.json ./frontend/
# Note: Uncomment the line below if you use package-lock.json (recommended for consistent installs)
# COPY frontend/package-lock.json ./frontend/
RUN --mount=type=cache,target=/root/.npm \
    cd frontend && npm install

# -----------------------------------------------------------------------------
# Runtime Setup
# -----------------------------------------------------------------------------

# Copy the container supervisor entrypoint.
COPY packaging/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Keep the settings and recordings directories on volumes by default.
VOLUME ["/app/config", "/app/recordings"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/local/bin/docker-entrypoint.sh"]

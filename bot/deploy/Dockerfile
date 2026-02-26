FROM python:3.13-slim-bookworm

# Install Node.js (for WhatsApp bridge) using direct binary download
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git ripgrep bubblewrap socat libfuse2 build-essential xz-utils && \
    # Download and install Node.js 20 directly
    ARCH=$(dpkg --print-architecture) && \
    NODE_VERSION=20.18.1 && \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${ARCH}.tar.xz" -o /tmp/node.tar.xz && \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && \
    rm /tmp/node.tar.xz && \
    # Install uv
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    # Clean up
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Make uv available in PATH
ENV PATH="/root/.local/bin:$PATH"
# Allow pip to install system-wide
ENV PIP_BREAK_SYSTEM_PACKAGES=1

WORKDIR /app

# ============================================
# Layer 1: Install dependencies (cached)
# ============================================

# Copy Python package file
COPY pyproject.toml ./

# Create empty vikingbot directory (required for install, will be overwritten later)
RUN mkdir -p vikingbot

# Copy bridge package files
COPY bridge/package*.json ./bridge/

# Install Python dependencies - use pip install for openviking (no local build)
ENV UV_HTTP_TIMEOUT=300
RUN uv pip install --system --no-cache "openviking>=0.1.18" && \
    uv pip install --system --no-cache .

# Install global npm packages for sandbox
RUN npm install -g @anthropic-ai/sandbox-runtime

# ============================================
# Layer 2: Copy source code and build
# ============================================

# Copy actual source code (overwrites empty directory)
COPY vikingbot/ vikingbot/

# Copy remaining project files
COPY README.md ./
COPY license/LICENSE ./LICENSE
COPY bridge/ bridge/

# Reinstall project package with actual source code
RUN uv pip install --system --no-cache .

# Build WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Create necessary directories
RUN mkdir -p /root/.vikingbot /root/.vikingbot/workspace /root/.vikingbot/sandboxes /root/.vikingbot/bridge

# Pre-build bridge to correct location
RUN cp -r /app/bridge/* /root/.vikingbot/bridge/ && \
    cd /root/.vikingbot/bridge && \
    npm install --omit=dev

# Console port (health check and web UI)
EXPOSE 18791

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production

# Set working directory
WORKDIR /app

ENTRYPOINT ["vikingbot"]
CMD ["status"]

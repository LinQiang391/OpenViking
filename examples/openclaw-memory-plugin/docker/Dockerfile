ARG OPENEULER_IMAGE=openeuler/openeuler:22.03-lts
ARG PYTHON_VERSION=3.11.12
ARG NODE_VERSION=22
ARG NVM_VERSION=v0.40.1
ARG PYTHON_SOURCE_BASE=https://mirrors.huaweicloud.com/python
ARG PYTHON_SOURCE_MIRROR_BASE=https://www.python.org/ftp/python
ARG GO_VERSION=1.25.6
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG GO_DIST_BASE=https://mirrors.aliyun.com/golang
ARG GO_DIST_MIRROR_BASE=https://go.dev/dl
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY_FALLBACK=https://registry.npmjs.org
ARG GOPROXY_URL=https://goproxy.cn,direct
ARG OPENCLAW_REPO_URL=https://github.com/openclaw/openclaw.git
ARG OPENCLAW_REF=main

FROM ${OPENEULER_IMAGE} AS python-builder
ARG PYTHON_VERSION
ARG PYTHON_SOURCE_BASE
ARG PYTHON_SOURCE_MIRROR_BASE

RUN unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    dnf install -y \
    gcc \
    make \
    findutils \
    tar \
    gzip \
    xz \
    wget \
    openssl-devel \
    bzip2-devel \
    libffi-devel \
    zlib-devel \
    readline-devel \
    sqlite-devel \
    xz-devel \
    tk-devel \
    ncurses-devel \
    gdbm-devel \
    libuuid-devel \
 && dnf clean all \
 && rm -rf /var/cache/dnf

WORKDIR /tmp
RUN wget -q "${PYTHON_SOURCE_BASE}/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" \
 || wget -q "${PYTHON_SOURCE_MIRROR_BASE}/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" \
 && tar -xzf "Python-${PYTHON_VERSION}.tgz" \
 && cd "Python-${PYTHON_VERSION}" \
 && ./configure --prefix=/usr/local --enable-optimizations --enable-shared LDFLAGS="-Wl,-rpath /usr/local/lib" \
 && make -j"$(nproc)" \
 && make altinstall \
 && cd /tmp \
 && rm -rf "/tmp/Python-${PYTHON_VERSION}" "/tmp/Python-${PYTHON_VERSION}.tgz"

FROM ${OPENEULER_IMAGE}
ARG PYTHON_VERSION
ARG NODE_VERSION
ARG NVM_VERSION
ARG GO_VERSION
ARG GO_DIST_BASE
ARG GO_DIST_MIRROR_BASE
ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG NPM_REGISTRY
ARG NPM_REGISTRY_FALLBACK
ARG GOPROXY_URL
ARG OPENCLAW_REPO_URL
ARG OPENCLAW_REF
ARG HTTP_PROXY
ARG HTTPS_PROXY

# 使用 OPENCLAW_STATE_DIR 直接指定状态目录，避免 OPENCLAW_HOME 导致的嵌套（/root/.openclaw/.openclaw/）
ENV OPENCLAW_STATE_DIR=/root/.openclaw
ENV NVM_DIR=/root/.nvm
ENV OPENVIKING_HOME=/root/.openviking
ENV OPENVIKING_CONFIG_FILE=/root/.openviking/ov.conf
ENV OPENVIKING_PYTHON=/usr/local/bin/python3
ENV OPENVIKING_GO_PATH=/usr/local/go/bin
ENV GOROOT=/usr/local/go
ENV GOPATH=/root/go
ENV PATH=/usr/local/go/bin:/root/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV GOPROXY=${GOPROXY_URL}
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
ENV npm_config_registry=${NPM_REGISTRY}
# 不设 npm_config_build_from_source=false，与 npm-only 一致，便于 sharp 在预编译失败时从源码编译
ENV npm_config_optional=true

# Base deps + openclaw 依赖（与 npm-only 测试一致：glib2 + make/cmake，仅 npm 安装 openclaw）
RUN unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    dnf install -y \
    ca-certificates \
    curl \
    gettext \
    git \
    unzip \
    gcc \
    gcc-c++ \
    make \
    cmake \
    glib2 \
    tar \
    gzip \
    xz \
    openssl-libs \
    bzip2-libs \
    libffi \
    zlib \
    readline \
    sqlite \
    xz-libs \
    ncurses-libs \
    gdbm \
    libuuid \
 && dnf clean all \
 && rm -rf /var/cache/dnf

COPY --from=python-builder /usr/local /usr/local
RUN echo "/usr/local/lib" > /etc/ld.so.conf.d/python-local.conf \
 && ldconfig \
 && ln -sf "/usr/local/bin/python${PYTHON_VERSION%.*}" /usr/local/bin/python3 \
 && ln -sf "/usr/local/bin/python${PYTHON_VERSION%.*}" /usr/local/bin/python \
 && ln -sf "/usr/local/bin/pip${PYTHON_VERSION%.*}" /usr/local/bin/pip3 \
 && ln -sf "/usr/local/bin/pip${PYTHON_VERSION%.*}" /usr/local/bin/pip \
 && python3 --version \
 && pip3 --version

# 安装 nvm（与 npm-only 一致；访问 GitHub 可走代理）
RUN mkdir -p "${NVM_DIR}" /tmp/nvm-dl \
    && (export HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" http_proxy="${HTTP_PROXY}" https_proxy="${HTTPS_PROXY}"; \
        curl -fsSL "https://github.com/nvm-sh/nvm/archive/refs/tags/${NVM_VERSION}.tar.gz" -o /tmp/nvm.tar.gz) \
    || (unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null; \
        curl -fsSL "https://github.com/nvm-sh/nvm/archive/refs/tags/${NVM_VERSION}.tar.gz" -o /tmp/nvm.tar.gz) \
    && tar -xzf /tmp/nvm.tar.gz -C /tmp/nvm-dl \
    && cp -a /tmp/nvm-dl/nvm-*/. "${NVM_DIR}" \
    && rm -rf /tmp/nvm-dl /tmp/nvm.tar.gz \
    && echo 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' >> /root/.bashrc

# Install Go (required for pip openviking AGFS build; removed in slim step)
RUN set -eux; \
    arch="$(uname -m)"; \
    case "${arch}" in x86_64) go_arch="amd64" ;; aarch64|arm64) go_arch="arm64" ;; *) echo "Unsupported: ${arch}"; exit 1 ;; esac; \
    go_pkg="go${GO_VERSION}.linux-${go_arch}.tar.gz"; \
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    curl -fsSLO "${GO_DIST_BASE}/${go_pkg}" || curl -fsSLO "${GO_DIST_MIRROR_BASE}/${go_pkg}"; \
    rm -rf /usr/local/go; tar -C /usr/local -xzf "${go_pkg}"; rm -f "${go_pkg}"; \
    go version

RUN unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    python3 -m pip install --no-cache-dir --upgrade setuptools wheel \
 && (python3 -m pip install --no-cache-dir openviking \
     || python3 -m pip install --no-cache-dir openviking -i https://pypi.org/simple)

# 用 nvm 安装 Node 并 npm 安装 openclaw；先装 node-gyp 以便 sharp 能从源码编译
RUN set -e; \
    . "${NVM_DIR}/nvm.sh" \
    && nvm install ${NODE_VERSION} \
    && nvm alias default ${NODE_VERSION} \
    && nvm use default \
    && node --version && npm --version \
    && unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true \
    && npm install -g node-gyp --registry="${NPM_REGISTRY}" \
    || npm install -g node-gyp --registry="${NPM_REGISTRY_FALLBACK}" \
    && npm install -g openclaw --registry="${NPM_REGISTRY}" \
    || npm install -g openclaw --registry="${NPM_REGISTRY_FALLBACK}" \
    && openclaw --version \
    && rm -rf /root/.npm /root/.cache /tmp/* 2>/dev/null || true \
    && NODE_BIN="$(. ${NVM_DIR}/nvm.sh && nvm which current)" \
    && BIN_DIR="$(dirname "${NODE_BIN}")" \
    && ln -sf "${NODE_BIN}" /usr/local/bin/node \
    && ln -sf "${BIN_DIR}/npm" /usr/local/bin/npm \
    && ln -sf "${BIN_DIR}/npx" /usr/local/bin/npx \
    && ([ -x "${BIN_DIR}/openclaw" ] && ln -sf "${BIN_DIR}/openclaw" /usr/local/bin/openclaw || true)

# Slim: remove build-only tools, Go, caches
RUN unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    dnf remove -y --skip-broken gcc gcc-c++ make cmake git 2>/dev/null || true; \
    dnf clean all && rm -rf /var/cache/dnf \
 && rm -rf /usr/local/go /root/go /root/.cache \
 && rm -rf /usr/local/include \
 && rm -rf /usr/local/share/man /usr/local/share/doc \
 && find /usr/local -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
 && find /usr/local -type f -name "*.pyc" -delete 2>/dev/null || true

ENV OPENVIKING_GO_PATH=
ENV GOROOT=
ENV GOPATH=
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN mkdir -p \
    /root/.openclaw/extensions/memory-openviking \
    /root/.openviking/data

COPY examples/openclaw-memory-plugin/index.ts /root/.openclaw/extensions/memory-openviking/index.ts
COPY examples/openclaw-memory-plugin/config.ts /root/.openclaw/extensions/memory-openviking/config.ts
COPY examples/openclaw-memory-plugin/openclaw.plugin.json /root/.openclaw/extensions/memory-openviking/openclaw.plugin.json
COPY examples/openclaw-memory-plugin/package.json /root/.openclaw/extensions/memory-openviking/package.json
COPY examples/openclaw-memory-plugin/package-lock.json /root/.openclaw/extensions/memory-openviking/package-lock.json

WORKDIR /root/.openclaw/extensions/memory-openviking
RUN unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; \
    npm install --omit=dev --no-audit --no-fund \
 && rm -rf /root/.npm 2>/dev/null || true

# 挂载 -v 宿主机目录:/root/.openclaw 时会遮住镜像内插件，entrypoint 从此处拷贝到挂载目录
RUN mkdir -p /app/openclaw-plugin-default \
 && cp -a /root/.openclaw/extensions/memory-openviking /app/openclaw-plugin-default/

WORKDIR /app
COPY examples/openclaw-memory-plugin/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY examples/openclaw-memory-plugin/docker/ov.conf.template.json /app/ov.conf.template.json
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 18789 1933

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

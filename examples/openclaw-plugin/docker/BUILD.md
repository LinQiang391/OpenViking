# OpenViking Docker 镜像构建指南

本文档介绍如何构建 **OpenViking 服务镜像** 和 **OpenClaw + OpenViking 插件镜像**。

## 目录结构

构建涉及的文件均位于 `examples/openclaw-plugin/docker/` 目录下：

```
examples/openclaw-plugin/docker/
├── Dockerfile.openviking                  # OpenViking 服务镜像（多阶段构建）
├── Dockerfile.remote                      # OpenClaw + OpenViking 插件镜像
├── build-openviking.sh                    # OpenViking 构建脚本
├── build-openclaw-plugin.sh               # OpenClaw 构建脚本
├── deploy.sh                              # 一键部署脚本
├── deploy.env                             # 部署配置文件
├── entrypoint-openviking.sh               # OpenViking 容器启动脚本
├── entrypoint-remote.sh                   # OpenClaw 容器启动脚本
├── ov.conf.template.json                  # OpenViking 配置模板
├── ov.conf.local-embed.template.json      # OpenViking 本地 embedding 配置模板
├── models/                                # 预置模型文件（构建时自动创建）
│   └── bge-small-zh-v1.5-f16.gguf        # BGE embedding 模型 (~46MB)
└── patches/                               # 构建依赖（需手动准备，不随仓库提交）
    ├── BiShengCompiler-*-aarch64-linux.tar.gz  # BiSheng 编译器 (~1.8GB)
    ├── gemm_opt_for_fp16_fp32.patch            # llama.cpp 性能优化补丁
    └── llama-cpp-python/                       # llama-cpp-python v0.3.9 源码（含子模块，已预打兼容补丁）
        └── vendor/llama.cpp/                   # llama.cpp 源码 (commit 3ac67535c86)
```

仓库根目录下还需要：

```
/                                          # OpenViking 仓库根目录
└── opengauss-minimal.patch                # openGauss 向量数据库后端补丁
```

---

## 一、构建 OpenViking 服务镜像

### 前置准备

构建前需在 `patches/` 目录下准备以下文件（均不随代码仓提交）：

#### 1. 准备 llama-cpp-python 源码（含 llama.cpp 子模块）

```bash
cd examples/openclaw-plugin/docker/patches/

# 克隆 llama-cpp-python v0.3.9（不拉子模块）
git clone \
    -c http.proxy=http://127.0.0.1:17897 \
    -c https.proxy=http://127.0.0.1:17897 \
    --branch v0.3.9 --no-recurse-submodules \
    https://github.com/abetlen/llama-cpp-python.git llama-cpp-python

# 克隆 llama.cpp 到子模块位置
git clone \
    -c http.proxy=http://127.0.0.1:17897 \
    -c https.proxy=http://127.0.0.1:17897 \
    https://github.com/ggerganov/llama.cpp.git llama-cpp-python/vendor/llama.cpp

cd llama-cpp-python/vendor/llama.cpp
git checkout 3ac67535c86
cd ../../..
```

> **注意**：保留 `.git` 目录，不要删除。构建过程中 `patch -p1` 需要在 git 仓库中工作。

#### 2. 准备补丁文件

| 文件 | 来源 | 说明 |
|------|------|------|
| `gemm_opt_for_fp16_fp32.patch` | 补丁提供方（华为鲲鹏） | llama.cpp GEMM 性能优化补丁 |

> **关于 API 兼容补丁**：`llama-cpp-python-compat.patch` 已预先应用到 `patches/llama-cpp-python/` 源码中，
> 无需单独准备。该补丁解决 llama.cpp 3ac67535c86 移除旧调试 API 后 Python 绑定的兼容性问题。

#### 3. 下载 BiSheng 编译器

```bash
cd examples/openclaw-plugin/docker/patches/

wget https://mirrors.huaweicloud.com/kunpeng/archive/compiler/bisheng_compiler/BiShengCompiler-5.0.0-aarch64-linux.tar.gz
```

> 文件约 1.8 GB，建议提前下载。构建脚本会自动将其拷入 Docker build context。

#### 4. 准备 BGE 模型文件（可选）

```bash
mkdir -p examples/openclaw-plugin/docker/models/

# 从 HuggingFace 下载
curl -fSL -o examples/openclaw-plugin/docker/models/bge-small-zh-v1.5-f16.gguf \
    "https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf/resolve/main/bge-small-zh-v1.5-f16.gguf?download=true"
```

> 约 46MB。如果本地已有则直接放入 `models/` 目录；如果不提供，构建时会自动在线下载。

#### 5. 确认 openGauss 补丁

确认仓库根目录下存在 `opengauss-minimal.patch`（该文件已随仓库提交）。

### 准备完成后的目录检查清单

```bash
# 运行以下命令确认所有文件就位
ls -lh examples/openclaw-plugin/docker/patches/BiShengCompiler-*-aarch64-linux.tar.gz
ls -lh examples/openclaw-plugin/docker/patches/gemm_opt_for_fp16_fp32.patch
ls -d  examples/openclaw-plugin/docker/patches/llama-cpp-python/vendor/llama.cpp
ls -lh opengauss-minimal.patch
# 可选
ls -lh examples/openclaw-plugin/docker/models/bge-small-zh-v1.5-f16.gguf 2>/dev/null || echo "(模型未预置，构建时在线下载)"
```

### 开始构建

```bash
cd examples/openclaw-plugin/docker/

# 基本构建（需要 HTTP 代理访问 GitHub/PyPI/crates.io）
HTTP_PROXY=http://127.0.0.1:17897 \
HTTPS_PROXY=http://127.0.0.1:17897 \
./build-openviking.sh --ov-ref v0.3.9

# 指定镜像 tag
./build-openviking.sh --ov-ref v0.3.9 --tag 0423

# 不使用 Docker 缓存（完全重建）
./build-openviking.sh --ov-ref v0.3.9 --no-cache

# 保留构建临时目录（调试用）
./build-openviking.sh --ov-ref v0.3.9 --keep-build-dir
```

### 构建流程说明

镜像采用三阶段构建，最终镜像不含源代码和补丁：

1. **Stage 1 (python-builder)**：从源码编译 Python 3.11.12
2. **Stage 2 (openviking-builder)**：编译所有 wheel
   - 安装 Go 1.22.6、Rust（OpenViking 编译依赖）
   - 安装 BiSheng 编译器（支持 hip09 目标架构）
   - 编译 OpenViking wheel
   - 编译 psycopg2-binary wheel（openGauss 驱动）
   - 编译 llama-cpp-python wheel（应用性能补丁，使用 BiSheng clang 编译）
3. **Stage 3 (最终镜像)**：精简运行时镜像
   - 仅包含：Python 运行时 + pip 包 + 配置模板 + 启动脚本 + GGUF 模型 + libomp.so
   - 不包含：源代码、补丁、编译器、Go/Rust 工具链、wheel 文件

### 编译参数

#### Docker 内 llama-cpp-python 编译参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 编译器 | BiSheng clang/clang++ 5.0.0 | 华为鲲鹏专用编译器，原生支持 hip09 |
| MCPU | `hip09+crypto+fp16+rcpc+sha3+sm4+bf16+nodotprod+noprofile+nopredres+nof32mm+nof64mm+dotprod+noi8mm+sve` | BiSheng 原生 hip09 优化标志 |
| 运行时依赖 | `libomp.so` | BiSheng LLVM OpenMP 库，需复制到最终镜像 |

#### 本地编译参数（非 Docker）

| 参数 | 值 | 说明 |
|------|-----|------|
| 编译器 | 系统 GCC 12.3.1 (`/usr/bin/gcc`) | 不要使用 openGauss 自带的 GCC 10.3.1 |
| MCPU | `neoverse-n2+crypto+fp16+rcpc+sha3+sm4+bf16+nodotprod+noi8mm+sve` | GCC 等效于 hip09 的架构名称 |

---

## 二、构建 OpenClaw + OpenViking 插件镜像

OpenClaw 镜像无需额外准备文件，直接从 npm 安装。

```bash
cd examples/openclaw-plugin/docker/

# 基本构建（需要 HTTP 代理）
HTTP_PROXY=http://127.0.0.1:17897 \
HTTPS_PROXY=http://127.0.0.1:17897 \
./build-openclaw-plugin.sh --tag 0423

# 指定 openclaw 版本
./build-openclaw-plugin.sh --openclaw-version 0.1.27 --tag 0423

# 指定插件源码分支
./build-openclaw-plugin.sh --plugin-branch opengauss-docker-new --tag 0423
```

---

## 三、一键部署

构建完镜像后，使用 `deploy.sh` 一键部署：

```bash
cd examples/openclaw-plugin/docker/

# 编辑配置文件
vi deploy.env

# 全量部署（openGauss + OpenViking + OpenClaw）
bash deploy.sh -password 'YourPassword123#'

# 仅部署 OpenViking + OpenClaw（不启动 openGauss）
# 需要在 deploy.env 中设置 ENABLE_OPENGAUSS="false"
bash deploy.sh

# 查看部署状态
bash deploy.sh --status

# 重启所有容器
bash deploy.sh --restart

# 清理所有容器
bash deploy.sh --cleanup
```

---

## 版本对应关系

| 组件 | 版本 / Commit |
|------|---------------|
| llama-cpp-python | v0.3.9 |
| llama.cpp | 3ac67535c86 |
| Python | 3.11.12 |
| Go | 1.22.6 |
| BiSheng Compiler | 5.0.0 |
| 基础镜像 | openeuler/openeuler:22.03-lts |

## 本地编译 llama.cpp（独立 C++ 程序）

不依赖 Python，直接编译 llama.cpp 可执行程序（如 `llama-embedding`、`llama-server` 等）：

```bash
cp -a examples/openclaw-plugin/docker/patches/llama-cpp-python/vendor/llama.cpp /tmp/llama-cpp-build
cd /tmp/llama-cpp-build

# 应用性能补丁
patch -p1 < /path/to/gemm_opt_for_fp16_fp32.patch

# 编译（使用系统 GCC 12.3.1，不要用 openGauss 自带的 GCC 10.3）
export CC=/usr/bin/gcc CXX=/usr/bin/g++ PATH="/usr/bin:$PATH"
MCPU="neoverse-n2+crypto+fp16+rcpc+sha3+sm4+bf16+nodotprod+noi8mm+sve"

CFLAGS="-O3 -funroll-loops -mcpu=$MCPU" \
CXXFLAGS="-O3 -funroll-loops -mcpu=$MCPU" \
cmake -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DLLAMA_CURL=OFF \
      -DGGML_CCACHE=OFF \
      -DGGML_NATIVE=OFF \
      -B build

cmake --build build --config release -j $(nproc)
```

编译产物在 `build/bin/` 下：

```bash
./build/bin/llama-embedding \
    -m /path/to/bge-small-zh-v1.5-f16.gguf \
    -p "hello world"

./build/bin/llama-server \
    -m /path/to/model.gguf \
    --host 0.0.0.0 --port 8080
```

## 本地编译 llama-cpp-python（Python wheel）

```bash
cp -a examples/openclaw-plugin/docker/patches/llama-cpp-python /tmp/lcpp-build
cd /tmp/lcpp-build

# 应用补丁
cd vendor/llama.cpp
patch -p1 < /path/to/gemm_opt_for_fp16_fp32.patch
cd ../..

# 编译（使用系统 GCC 12.3.1）
export CC=/usr/bin/gcc CXX=/usr/bin/g++ PATH="/usr/bin:$PATH"
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
MCPU="neoverse-n2+crypto+fp16+rcpc+sha3+sm4+bf16+nodotprod+noi8mm+sve"

CMAKE_ARGS="-DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DLLAMA_CURL=OFF -DGGML_CCACHE=OFF \
  -DLLAVA_BUILD=OFF -DGGML_NATIVE=OFF" \
CFLAGS="-O3 -funroll-loops -mcpu=$MCPU" \
CXXFLAGS="-O3 -funroll-loops -mcpu=$MCPU" \
pip3 wheel --no-cache-dir --wheel-dir=./wheels . \
  -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
```

> **已知问题**：llama-cpp-python v0.3.9 在 AArch64 + openEuler 环境下通过 Python ctypes 调用
> `llama_encode`（BERT 类模型）时会触发 SIGSEGV 段错误。同一个 `.so` 文件从 C 程序调用完全正常。
> 该问题与性能补丁无关（不打补丁也崩）、与编译器无关（GCC/BiSheng 都崩）、与 SVE 无关（关掉也崩）。
> 建议使用远程 embedding 服务或独立部署 `llama-server` 作为 embedding 后端来规避。

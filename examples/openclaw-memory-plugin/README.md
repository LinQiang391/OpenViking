# OpenClaw + OpenViking Memory Plugin

将 [OpenViking](https://github.com/openviking/OpenViking) 作为 [OpenClaw](https://github.com/openclaw/openclaw) 的长期记忆后端。

- **Docker 使用**：打包镜像、启动容器、环境变量与持久化说明见 **[docker/README.md](docker/README.md)**。
- **本地安装**：需已安装 OpenClaw（`npm install -g openclaw`）与 Python ≥ 3.10 及 openviking（`pip install openviking`）。在 OpenViking 仓库根目录执行 `npx ./examples/openclaw-memory-plugin/setup-helper`，然后 `openclaw gateway`。详见 [INSTALL-ZH.md](INSTALL-ZH.md)。

# VKE 部署指南

## 概述

本文档介绍如何在火山引擎 VKE（火山引擎 Kubernetes）上部署 vikingbot。

## 前置要求

- Docker
- kubectl
- Python 3.7+
- 火山引擎账号及 Access Key
- VKE 集群已创建
- csi-tos 组件已安装

## 部署流程

### 步骤一：创建 VKE 集群

1. **登录火山引擎控制台**
   - 访问：https://console.volcengine.com/vke

2. **创建集群**
   - 点击「创建集群」
   - **基本配置**：
     - 集群名称：`vikingbot`（或自定义）
     - 地域：选择你的地域，如 `华北2（北京）`
     - Kubernetes 版本：选择推荐版本
   - **节点配置**：
     - 容器网络模型：`Flannel`
     - Service CIDR：保持默认
     - 域名：保持默认
   - **组件配置**：
     - **必须安装**：`csi-tos`（用于 TOS 对象存储）
     - 其他组件按需选择
   - 点击「下一步」

3. **创建节点池**
   - **节点池配置**：
     - 节点池名称：`default`
     - 可用区：选择和集群同一地域的可用区
   - **实例规格**：
     - 推荐：2核4GB或更高
     - 实例类型：按需计费
   - **节点数量**：
     - 初始数量：2-3个节点
   - **系统配置**：
     - 系统盘：40GB+
   - 点击「下一步」->「确认配置」->「创建」

4. **等待集群创建完成**
   - 等待 5-10 分钟，集群状态变为「运行中」

### 步骤二：配置部署

1. **安装依赖**
   ```bash
   pip install pyyaml requests
   ```

2. **配置**
   首次运行会自动创建配置文件：
   ```bash
   python deploy/vke/vke_deploy.py
   ```

   或手动复制：
   ```bash
   cp deploy/vke/vke_deploy.example.yaml ~/.config/vikingbot/vke_deploy.yaml
   ```

   编辑 `~/.config/vikingbot/vke_deploy.yaml`，填入你的信息：

   | 配置项 | 说明 |
   |--------|------|
   | `volcengine_access_key` | 火山引擎 Access Key |
   | `volcengine_secret_key` | 火山引擎 Secret Key |
   | `volcengine_region` | 地域，如 `cn-beijing` |
   | `vke_cluster_id` | VKE 集群 ID（在控制台集群详情页查看） |
   | `image_registry` | 镜像仓库地址 |
   | `image_namespace` | 镜像命名空间 |
   | `image_repository` | 镜像仓库名 |
   | `image_tag` | 镜像标签 |

3. **获取 kubeconfig**
   1. 在 VKE 控制台找到你的集群
   2. 点击「连接集群」->「生成 KubeConfig」
   3. 下载并保存到 `~/.kube/config`

4. **执行部署**
   ```bash
   # 完整部署流程
   python deploy/vke/vke_deploy.py

   # 跳过镜像构建，只部署
   python deploy/vke/vke_deploy.py --skip-build --skip-push

   # 指定镜像 tag
   python deploy/vke/vke_deploy.py --image-tag v1.0.0
   ```

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--config, -c` | 配置文件路径 (默认: `~/.config/vikingbot/vke_deploy.yaml`) |
| `--skip-build` | 跳过镜像构建 |
| `--skip-push` | 跳过镜像推送 |
| `--skip-deploy` | 跳过 VKE 部署 |
| `--skip-image-check` | 跳过镜像存在检查 |
| `--image-tag` | 覆盖配置中的镜像 tag |

## 部署流程

1. **构建 Docker 镜像** - 使用 `deploy/Dockerfile`
2. **登录镜像仓库** - 登录火山引擎镜像仓库
3. **推送镜像** - 推送镜像到仓库
4. **获取 kubeconfig** - 使用配置的 kubeconfig
5. **部署到 VKE** - 应用 K8s manifest，包含共享 PVC
6. **等待部署完成** - 可选，等待 rollout 完成

## 共享存储说明

使用 `csi-tos` 存储类，多个 Pod 可以同时挂载并共享 `/root/.vikingbot` 目录：

- `config.json` - 配置文件共享
- `workspace/` - 工作区共享
- `sandboxes/` - sandbox 数据共享
- `bridge/` - WhatsApp bridge 共享

## 注意事项

### 安全建议

1. 不要将 `vke_deploy.yaml` 提交到版本控制
2. 使用环境变量或密钥管理服务存储敏感信息
3. 为部署使用专门的 IAM 账号，授予最小权限
4. 定期轮换 Access Key

### 验证部署

```bash
kubectl get pods -n default
kubectl rollout status deployment/vikingbot -n default
kubectl get pvc -n default
```

### 查看部署日志

```bash
kubectl logs -f deployment/vikingbot -n default
```

## 常见问题

### Q: 部署卡住，如何查看错误？

脚本会自动收集诊断信息：
- Pod 状态
- Pod 事件
- Deployment 详情
- Pod 日志

### Q: 如何只重新部署而不重新构建？

```bash
python deploy/vke/vke_deploy.py --skip-build --skip-push --image-tag v1.0.0
```

### Q: 镜像仓库登录失败？

确保：
- Access Key/Secret Key 正确
- 账号有镜像仓库权限
- 镜像仓库地址格式正确

## 相关文档

- [火山引擎 VKE 文档](https://www.volcengine.com/docs/6460)
- [火山引擎镜像仓库](https://www.volcengine.com/docs/6424)
- [VKE TOS 存储](https://www.volcengine.com/docs/6460/101643)
- [Kubernetes 文档](https://kubernetes.io/docs/)

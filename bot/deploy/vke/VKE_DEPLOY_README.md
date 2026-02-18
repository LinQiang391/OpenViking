# Vikingbot VKE 部署指南

## 前置要求

- Docker
- kubectl
- Python 3.7+
- 火山引擎账号及Access Key
- VKE集群已创建（见下方步骤）
- csi-tos组件已安装

## 目录结构

```
deploy/
├── local/          # 本地Docker相关（原docker目录）
├── ecs/            # ECS部署（待实现）
└── vke/            # VKE部署
    ├── vke_deploy.py          # 一键部署脚本
    ├── vke_deploy.example.yaml # 配置示例
    ├── VKE_DEPLOY_README.md   # 本文档
    └── k8s/
        └── deployment.yaml     # TOS存储版本
```

## 步骤一：创建VKE集群

### 1. 登录火山引擎控制台

访问：https://console.volcengine.com/vke

### 2. 创建集群

1. 点击「创建集群」

2. **基本配置**：
   - 集群名称：`vikingbot`（或自定义）
   - 地域：选择你的地域，如 `华北2（北京）`
   - Kubernetes版本：选择推荐版本

3. **节点配置**：
   - 容器网络模型：`Flannel`
   - Service CIDR：保持默认
   - 域名：保持默认

4. **组件配置**：
   - **必须安装**：`csi-tos`（用于TOS对象存储）
   - 其他组件按需选择

5. 点击「下一步」

### 3. 创建节点池

1. **节点池配置**：
   - 节点池名称：`default`
   - 可用区：选择和集群同一地域的可用区

2. **实例规格**：
   - 推荐：2核4GB或更高
   - 实例类型：按需计费

3. **节点数量**：
   - 初始数量：2-3个节点

4. **系统配置**：
   - 系统盘：40GB+

5. 点击「下一步」->「确认配置」->「创建」

### 4. 等待集群创建完成

等待5-10分钟，集群状态变为「运行中」

---

## 步骤二：配置部署

### 1. 安装依赖

```bash
pip install pyyaml requests
```

### 2. 配置

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
| `volcengine_access_key` | 火山引擎Access Key |
| `volcengine_secret_key` | 火山引擎Secret Key |
| `volcengine_region` | 地域，如 `cn-beijing` |
| `vke_cluster_id` | VKE集群ID（在控制台集群详情页查看） |
| `image_registry` | 镜像仓库地址 |
| `image_namespace` | 镜像命名空间 |
| `image_repository` | 镜像仓库名 |
| `image_tag` | 镜像标签 |

### 3. 获取kubeconfig

1. 在VKE控制台找到你的集群
2. 点击「连接集群」->「生成KubeConfig」
3. 下载并保存到 `~/.kube/config`

### 4. 执行部署

```bash
# 完整部署流程
python deploy/vke/vke_deploy.py

# 跳过镜像构建，只部署
python deploy/vke/vke_deploy.py --skip-build --skip-push

# 指定镜像tag
python deploy/vke/vke_deploy.py --image-tag v1.0.0
```

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--config, -c` | 配置文件路径 (默认: `~/.config/vikingbot/vke_deploy.yaml`) |
| `--skip-build` | 跳过镜像构建 |
| `--skip-push` | 跳过镜像推送 |
| `--skip-deploy` | 跳过VKE部署 |
| `--skip-image-check` | 跳过镜像存在检查 |
| `--image-tag` | 覆盖配置中的镜像tag |

---

## 部署流程

1. **构建Docker镜像** - 使用 `deploy/Dockerfile`
2. **登录镜像仓库** - 登录火山引擎镜像仓库
3. **推送镜像** - 推送镜像到仓库
4. **获取kubeconfig** - 使用配置的kubeconfig
5. **部署到VKE** - 应用K8s manifest，包含共享PVC
6. **等待部署完成** - 可选，等待rollout完成

---

## 共享存储说明

使用 `csi-tos` 存储类，多个Pod可以同时挂载并共享 `/root/.vikingbot` 目录：

- config.json - 配置文件共享
- workspace/ - 工作区共享
- sandboxes/ - sandbox数据共享
- bridge/ - WhatsApp bridge共享

---

## 常见问题

### Q: 部署卡住，如何查看错误？

脚本会自动收集诊断信息：
- Pod状态
- Pod事件
- Deployment详情
- Pod日志

### Q: 如何只重新部署而不重新构建？

```bash
python deploy/vke/vke_deploy.py --skip-build --skip-push --image-tag v1.0.0
```

### Q: 镜像仓库登录失败？

确保：
- Access Key/Secret Key正确
- 账号有镜像仓库权限
- 镜像仓库地址格式正确

### Q: 如何验证部署成功？

```bash
kubectl get pods -n default
kubectl rollout status deployment/vikingbot -n default
kubectl get pvc -n default
```

### Q: 如何查看部署日志？

```bash
kubectl logs -f deployment/vikingbot -n default
```

---

## 安全建议

1. 不要将 `vke_deploy.yaml` 提交到版本控制
2. 使用环境变量或密钥管理服务存储敏感信息
3. 为部署使用专门的IAM账号，授予最小权限
4. 定期轮换Access Key

---

## 相关文档

- [火山引擎VKE文档](https://www.volcengine.com/docs/6460)
- [火山引擎镜像仓库](https://www.volcengine.com/docs/6424)
- [VKE TOS存储](https://www.volcengine.com/docs/6460/101643)
- [Kubernetes文档](https://kubernetes.io/docs/)

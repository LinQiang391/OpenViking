#!/usr/bin/env python3

import argparse
import os
import sys
import json
import subprocess
import time
from typing import Optional, Dict, Any

HAS_YAML = False
yaml_module = None
try:
    import yaml
    HAS_YAML = True
    yaml_module = yaml
except ImportError:
    pass


class VKEDeployer:
    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_path = self.get_default_config_path()
        self.config_path = config_path
        print(f"使用配置文件: {config_path}")
        self.config = self.load_config(config_path)
        
        use_timestamp_tag = self.config.get("use_timestamp_tag", False)
        if use_timestamp_tag:
            import datetime
            now = datetime.datetime.now()
            timestamp_tag = now.strftime("build-%Y%m%d-%H%M%S")
            self.config["image_tag"] = timestamp_tag
            print(f"已启用时间戳标签，自动生成: {timestamp_tag}")
        
        self.validate_config()
        self.print_config_summary()

    def get_default_config_path(self) -> str:
        config_dir = os.path.expanduser("~/.config/vikingbot")
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "vke_deploy.yaml")

    def load_config(self, config_path: str) -> Dict[str, Any]:
        if not os.path.exists(config_path):
            print(f"配置文件不存在: {config_path}")
            print(f"正在创建默认配置文件...")
            self.create_default_config(config_path)
            print(f"\n已创建默认配置文件: {config_path}")
            print("请编辑该文件，填入你的配置信息后重新运行脚本。")
            sys.exit(1)

        with open(config_path, 'r', encoding='utf-8') as f:
            if config_path.endswith('.json'):
                return json.load(f)
            elif HAS_YAML and yaml_module is not None:
                return yaml_module.safe_load(f)
            else:
                print(f"配置文件格式不支持，且未安装pyyaml。请安装: pip install pyyaml")
                sys.exit(1)

    def create_default_config(self, config_path: str):
        example_config_path = os.path.join(os.path.dirname(__file__), "vke_deploy.example.yaml")

        if os.path.exists(example_config_path):
            with open(example_config_path, 'r', encoding='utf-8') as src:
                content = src.read()
        else:
            content = """# Vikingbot VKE 部署配置
# 请填入你的配置信息

volcengine_access_key: AKLTxxxxxxxxxx
volcengine_secret_key: xxxxxxxxxx
volcengine_region: cn-beijing

vke_cluster_id: ccxxxxxxxxxx

image_registry: vikingbot-cn-beijing.cr.volces.com
image_namespace: vikingbot
image_repository: vikingbot
# 镜像标签配置
# use_timestamp_tag: 是否使用时间戳标签 (true/false)
#   - true: 自动生成时间戳标签，格式: build-YYYYMMDD-HHMMSS
#   - false: 使用 image_tag 指定的标签
use_timestamp_tag: false
# image_tag: 固定标签 (仅当 use_timestamp_tag: false 时生效)
#   - 可以设置为: latest, v1.0.0, build-123 等
image_tag: latest
local_image_name: vikingbot

registry_username: ""
registry_password: ""

dockerfile_path: deploy/Dockerfile
build_context: .

# K8s manifest文件
k8s_manifest_path: deploy/vke/k8s/deployment.yaml
k8s_namespace: default
k8s_deployment_name: vikingbot
k8s_replicas: 1

kubeconfig_path: ~/.kube/config

wait_for_rollout: true
rollout_timeout: 300

# 如果本地镜像已存在，是否跳过检查和重新构建
# skip_image_check: false

# 存储类型选择
# 可选值: local (本地存储, 默认), tos (对象存储, 需要手动创建PV), nas (文件存储, 需要NAS实例)
storage_type: local

# TOS配置 (仅当storage_type=tos时需要)
tos_bucket: vikingbot_data
tos_path: /.vikingbot/
tos_region: cn-beijing

# NAS配置 (仅当storage_type=nas时需要)
# nas_server: your-nas-server-address
# nas_path: /your/nas/path

# OpenSandbox 配置
# 是否启用 OpenSandbox Sidecar 容器
opensandbox_enabled: true
# OpenSandbox Server 镜像源 (Docker Hub)
opensandbox_source_image: opensandbox/server:latest
# OpenSandbox Server 镜像在火山引擎仓库中的名称
opensandbox_repository: opensandbox-server
"""

        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def validate_config(self):
        required_fields = [
            "volcengine_access_key", "volcengine_secret_key",
            "volcengine_region", "vke_cluster_id",
            "image_registry", "image_namespace", "image_repository", "image_tag"
        ]

        missing_fields = []
        for field in required_fields:
            if field not in self.config or not self.config[field] or self.config[field] in ["AKLTxxxxxxxxxx", "xxxxxxxxxx", "ccxxxxxxxxxx"]:
                missing_fields.append(field)

        if missing_fields:
            print("\n配置验证失败！缺少或未更新以下字段：")
            for field in missing_fields:
                print(f"  - {field}")
            print(f"\n请编辑配置文件: {self.config_path}")
            sys.exit(1)

        print("配置验证通过！")

    def print_config_summary(self):
        print("\n当前配置摘要：")
        print(f"  地域: {self.config.get('volcengine_region')}")
        print(f"  集群ID: {self.config.get('vke_cluster_id')}")
        print(f"  镜像: {self.config.get('image_registry')}/{self.config.get('image_namespace')}/{self.config.get('image_repository')}:{self.config.get('image_tag')}")
        use_timestamp_tag = self.config.get("use_timestamp_tag", False)
        print(f"  时间戳标签: {'启用' if use_timestamp_tag else '禁用'}")
        print(f"  Dockerfile: {self.config.get('dockerfile_path', 'deploy/Dockerfile')}")
        print(f"  K8s manifest: {self.config.get('k8s_manifest_path', 'deploy/vke/k8s/deployment.yaml')}")
        print(f"  存储类型: {self.config.get('storage_type', 'local')}")
        opensandbox_enabled = self.config.get('opensandbox_enabled', True)
        print(f"  OpenSandbox: {'启用' if opensandbox_enabled else '禁用'}")
        if opensandbox_enabled:
            print(f"  OpenSandbox 源镜像: {self.config.get('opensandbox_source_image', 'opensandbox/server:latest')}")
        print()

    def run_command(self, cmd: str, cwd: Optional[str] = None, show_output: bool = False, timeout: Optional[float] = 60.0) -> tuple[int, str, str]:
        print(f"执行命令: {cmd}")
        
        if show_output:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            stdout_lines = []
            try:
                if proc.stdout:
                    for line in iter(proc.stdout.readline, ''):
                        print(line, end='')
                        stdout_lines.append(line)
                stdout = ''.join(stdout_lines)
                proc.wait(timeout=timeout)
                return proc.returncode, stdout, ''
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout = ''.join(stdout_lines)
                return -1, stdout, "Command timed out"
        else:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                text=True
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                return -1, stdout, stderr

    def check_image_exists(self, image_name: str, image_tag: str) -> bool:
        cmd = f"docker images -q {image_name}:{image_tag}"
        code, stdout, stderr = self.run_command(cmd)
        return code == 0 and stdout.strip() != ""

    def build_image(self) -> bool:
        print("\n=== 步骤1: 构建Docker镜像 ===")

        dockerfile_path = self.config.get("dockerfile_path", "deploy/Dockerfile")
        context_path = self.config.get("build_context", ".")
        local_image_name = self.config.get("local_image_name", "vikingbot")
        image_tag = self.config["image_tag"]
        full_local_image = f"{local_image_name}:{image_tag}"
        skip_image_check = self.config.get("skip_image_check", False)

        if not os.path.exists(dockerfile_path):
            print(f"Dockerfile不存在: {dockerfile_path}")
            return False

        if not skip_image_check and self.check_image_exists(local_image_name, image_tag):
            print(f"镜像已存在: {full_local_image}")
            print("直接重新构建 (如需跳过，请在配置文件中设置 skip_image_check: true)")

        cmd = f"docker build -f {dockerfile_path} -t {full_local_image} --platform linux/amd64 {context_path}"
        code, stdout, stderr = self.run_command(cmd, show_output=True)

        if code != 0:
            print(f"镜像构建失败")
            return False

        print(f"镜像构建成功: {full_local_image}")
        return True

    def login_registry(self) -> bool:
        print("\n=== 步骤2: 登录镜像仓库 ===")

        registry = self.config["image_registry"]
        username = self.config.get("registry_username", self.config["volcengine_access_key"])
        password = self.config.get("registry_password", self.config["volcengine_secret_key"])

        cmd = f"docker login -u {username} -p {password} {registry}"
        code, stdout, stderr = self.run_command(cmd)

        if code != 0:
            print(f"镜像仓库登录失败: {stderr}")
            return False

        print("镜像仓库登录成功")
        return True

    def push_image(self) -> bool:
        print("\n=== 步骤3: 推送镜像 ===")

        local_image_name = self.config.get("local_image_name", "vikingbot")
        image_tag = self.config["image_tag"]
        
        registry = self.config["image_registry"]
        namespace = self.config.get("image_namespace", "vikingbot")
        repository = self.config.get("image_repository", "vikingbot")
        full_image_name = f"{registry}/{namespace}/{repository}:{image_tag}"

        print("打标签...")
        cmd = f"docker tag {local_image_name}:{image_tag} {full_image_name}"
        code, stdout, stderr = self.run_command(cmd)
        if code != 0:
            print(f"打标签失败: {stderr}")
            return False

        print("推送镜像...")
        cmd = f"docker push {full_image_name}"
        code, stdout, stderr = self.run_command(cmd, show_output=True)

        if code != 0:
            print(f"镜像推送失败")
            return False

        print(f"镜像推送成功: {full_image_name}")
        self.config["full_image_name"] = full_image_name
        return True

    def prepare_opensandbox_image(self) -> bool:
        if not self.config.get("opensandbox_enabled", True):
            print("\n=== OpenSandbox 已禁用，跳过 ===")
            return True

        print("\n=== 准备 OpenSandbox 镜像 ===")

        source_image = self.config.get("opensandbox_source_image", "opensandbox/server:latest")
        registry = self.config["image_registry"]
        namespace = self.config.get("image_namespace", "vikingbot")
        repository = self.config.get("opensandbox_repository", "opensandbox-server")
        target_image = f"{registry}/{namespace}/{repository}:latest"

        print(f"拉取源镜像: {source_image}")
        cmd = f"docker pull --platform linux/amd64 {source_image}"
        code, stdout, stderr = self.run_command(cmd, show_output=True)
        if code != 0:
            print(f"拉取 OpenSandbox 镜像失败")
            return False

        print(f"打标签: {target_image}")
        cmd = f"docker tag {source_image} {target_image}"
        code, stdout, stderr = self.run_command(cmd)
        if code != 0:
            print(f"打标签失败: {stderr}")
            return False

        print(f"推送镜像: {target_image}")
        cmd = f"docker push {target_image}"
        code, stdout, stderr = self.run_command(cmd, show_output=True)
        if code != 0:
            print(f"推送 OpenSandbox 镜像失败")
            return False

        print(f"OpenSandbox 镜像准备成功: {target_image}")
        self.config["opensandbox_full_image"] = target_image
        return True

    def create_k8s_resources(self, kubeconfig_path: str, k8s_namespace: str) -> bool:
        print("\n=== 创建 Kubernetes 资源 ===")
        
        os.environ["KUBECONFIG"] = kubeconfig_path

        # 创建 Image Pull Secret
        print("创建 Image Pull Secret...")
        registry = self.config["image_registry"]
        username = self.config.get("registry_username", self.config["volcengine_access_key"])
        password = self.config.get("registry_password", self.config["volcengine_secret_key"])
        
        # 检查 secret 是否已存在
        cmd = f"kubectl get secret vikingbot-cr-secret -n {k8s_namespace} --ignore-not-found=true -o name"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout.strip():
            print("Image Pull Secret 已存在，跳过创建")
        else:
            cmd = f"kubectl create secret docker-registry vikingbot-cr-secret --docker-server={registry} --docker-username={username} --docker-password={password} -n {k8s_namespace}"
            code, stdout, stderr = self.run_command(cmd)
            if code != 0:
                print(f"创建 Image Pull Secret 失败: {stderr}")
                return False
            print("Image Pull Secret 创建成功")

        # 创建 ServiceAccount
        print("创建 ServiceAccount...")
        cmd = f"kubectl get serviceaccount vikingbot-sa -n {k8s_namespace} --ignore-not-found=true -o name"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout.strip():
            print("ServiceAccount 已存在，跳过创建")
        else:
            sa_yaml = f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: vikingbot-sa
  namespace: {k8s_namespace}
"""
            with open("/tmp/vikingbot_sa.yaml", "w", encoding='utf-8') as f:
                f.write(sa_yaml)
            cmd = f"kubectl apply -f /tmp/vikingbot_sa.yaml"
            code, stdout, stderr = self.run_command(cmd)
            if code != 0:
                print(f"创建 ServiceAccount 失败: {stderr}")
                return False
            print("ServiceAccount 创建成功")

        # 创建 ClusterRole
        print("创建 ClusterRole...")
        cmd = f"kubectl get clusterrole vikingbot-clusterrole --ignore-not-found=true -o name"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout.strip():
            print("ClusterRole 已存在，跳过创建")
        else:
            cr_yaml = f"""apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: vikingbot-clusterrole
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log", "pods/exec", "secrets", "configmaps"]
  verbs: ["get", "list", "watch", "create", "update", "delete"]
- apiGroups: ["apps"]
  resources: ["deployments", "statefulsets"]
  verbs: ["get", "list", "watch"]
"""
            with open("/tmp/vikingbot_cr.yaml", "w", encoding='utf-8') as f:
                f.write(cr_yaml)
            cmd = f"kubectl apply -f /tmp/vikingbot_cr.yaml"
            code, stdout, stderr = self.run_command(cmd)
            if code != 0:
                print(f"创建 ClusterRole 失败: {stderr}")
                return False
            print("ClusterRole 创建成功")

        # 创建 ClusterRoleBinding
        print("创建 ClusterRoleBinding...")
        cmd = f"kubectl get clusterrolebinding vikingbot-clusterrolebinding --ignore-not-found=true -o name"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout.strip():
            print("ClusterRoleBinding 已存在，跳过创建")
        else:
            crb_yaml = f"""apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: vikingbot-clusterrolebinding
subjects:
- kind: ServiceAccount
  name: vikingbot-sa
  namespace: {k8s_namespace}
roleRef:
  kind: ClusterRole
  name: vikingbot-clusterrole
  apiGroup: rbac.authorization.k8s.io
"""
            with open("/tmp/vikingbot_crb.yaml", "w", encoding='utf-8') as f:
                f.write(crb_yaml)
            cmd = f"kubectl apply -f /tmp/vikingbot_crb.yaml"
            code, stdout, stderr = self.run_command(cmd)
            if code != 0:
                print(f"创建 ClusterRoleBinding 失败: {stderr}")
                return False
            print("ClusterRoleBinding 创建成功")

        return True

    def get_vke_kubeconfig(self) -> Optional[str]:
        print("\n=== 步骤4: 获取VKE集群kubeconfig ===")

        kubeconfig_path = self.config.get("kubeconfig_path", "~/.kube/config")
        kubeconfig_path = os.path.expanduser(kubeconfig_path)

        if os.path.exists(kubeconfig_path):
            print(f"使用现有kubeconfig: {kubeconfig_path}")
            return kubeconfig_path

        print("\n未找到kubeconfig，请按以下步骤获取：")
        print("1. 访问火山引擎VKE控制台: https://console.volcengine.com/vke")
        print(f"2. 找到集群: {self.config['vke_cluster_id']}")
        print("3. 点击 \"连接集群\" -> \"生成KubeConfig\"")
        print(f"4. 保存到: {kubeconfig_path}")
        print("\n或者修改配置文件指定kubeconfig_path")

        return None

    def check_pvc_exists(self, namespace: str, pvc_name: str = "vikingbot-data") -> bool:
        cmd = f"kubectl get pvc {pvc_name} -n {namespace} --no-headers 2>/dev/null || true"
        code, stdout, stderr = self.run_command(cmd, timeout=60.0)
        return code == 0 and stdout.strip() != ""

    def deploy_to_vke(self, kubeconfig_path: str) -> bool:
        print("\n=== 步骤5: 部署应用到VKE ===")

        manifest_path = self.config.get("k8s_manifest_path", "deploy/vke/k8s/deployment.yaml")

        if not os.path.exists(manifest_path):
            print(f"K8s manifest不存在: {manifest_path}")
            return False

        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest_content = f.read()

        registry = self.config["image_registry"]
        namespace = self.config.get("image_namespace", "vikingbot")
        repository = self.config.get("image_repository", "vikingbot")
        full_image_name = f"{registry}/{namespace}/{repository}:{self.config['image_tag']}"
        
        modified = False
        
        if "__IMAGE_NAME__" in manifest_content:
            manifest_content = manifest_content.replace("__IMAGE_NAME__", full_image_name)
            print(f"已替换镜像为: {full_image_name}")
            modified = True
        
        k8s_replicas = self.config.get("k8s_replicas")
        if k8s_replicas is not None:
            import re
            manifest_content = re.sub(
                r'replicas:\s*\d+', 
                f'replicas: {k8s_replicas}', 
                manifest_content
            )
            print(f"已设置副本数为: {k8s_replicas}")
            modified = True
        
        opensandbox_enabled = self.config.get("opensandbox_enabled", True)
        if not opensandbox_enabled:
            print("OpenSandbox 已禁用，移除 Sidecar 容器...")
            import re
            manifest_content = re.sub(
                r'(\n\s+-\s+name:\s+opensandbox-server.*?(?=\n\s+-\s+name:|\n\s+volumes:)', 
                '', 
                manifest_content, 
                flags=re.DOTALL
            )
            manifest_content = re.sub(
                r'\n\s+- name: NANOBOT_SANDBOX__OPENSANDBOX__SERVER_URL.*?\n', '', manifest_content)
            manifest_content = re.sub(
                r'\n\s+- name: NANOBOT_SANDBOX__OPENSANDBOX__TOS__ENABLED.*?\n', '', manifest_content)
            manifest_content = re.sub(
                r'\n\s+- name: NANOBOT_SANDBOX__OPENSANDBOX__TOS__PVC_NAME.*?\n', '', manifest_content)
            modified = True
            print("已移除 OpenSandbox Sidecar 容器")
        else:
            opensandbox_image = self.config.get("opensandbox_full_image")
            if not opensandbox_image:
                registry = self.config["image_registry"]
                namespace = self.config.get("image_namespace", "vikingbot")
                repository = self.config.get("opensandbox_repository", "opensandbox-server")
                opensandbox_image = f"{registry}/{namespace}/{repository}:latest"
            
            if "opensandbox/server:latest" in manifest_content:
                manifest_content = manifest_content.replace("opensandbox/server:latest", opensandbox_image)
                print(f"已设置 OpenSandbox 镜像为: {opensandbox_image}")
                modified = True
            
            # 添加 imagePullSecrets 和 serviceAccountName
            if "imagePullSecrets:" not in manifest_content:
                import re
                # 在 spec.containers 之前添加
                manifest_content = re.sub(
                    r'(\n\s+spec:\s*\n)',
                    r'\1      imagePullSecrets:\n      - name: vikingbot-cr-secret\n      serviceAccountName: vikingbot-sa\n',
                    manifest_content
                )
                modified = True
        
        if modified:
            temp_manifest = "/tmp/vke_deploy_temp.yaml"
            with open(temp_manifest, 'w', encoding='utf-8') as f:
                f.write(manifest_content)
            deploy_path = temp_manifest
        else:
            deploy_path = manifest_path

        os.environ["KUBECONFIG"] = kubeconfig_path

        k8s_namespace = self.config.get("k8s_namespace", "default")
        
        storage_type = self.config.get("storage_type", "local")
        pvc_exists = self.check_pvc_exists(k8s_namespace)
        
        if storage_type == "tos":
            # If storage type is TOS, use our own PV/PVC instead of the one in the manifest
            tos_bucket = self.config.get("tos_bucket", "vikingbot_data")
            tos_path = self.config.get("tos_path", "/.vikingbot/")
            tos_region = self.config.get("tos_region", self.config.get("volcengine_region", "cn-beijing"))
            
            # Now, check if our PV/PVC exist, and if not, create them
            pv_name = "vikingbot-tos-pv"
            pvc_name = "vikingbot-data"
            
            # Check if PV exists
            cmd = f"kubectl get pv {pv_name} --ignore-not-found=true -o name"
            code, stdout, stderr = self.run_command(cmd)
            pv_exists = code == 0 and stdout.strip() != ""
            
            if not pv_exists:
                secret_name = "secret-tos-aksk"
                print(f"Creating secret {secret_name} for TOS...")
                secret_yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
  namespace: {k8s_namespace}
type: Opaque
stringData:
  accessKeyId: {self.config['volcengine_access_key']}
  accessKeySecret: {self.config['volcengine_secret_key']}
"""
                temp_secret_file = "/tmp/vke_deploy_secret.yaml"
                with open(temp_secret_file, "w", encoding="utf-8") as f:
                    f.write(secret_yaml)
                print(f"Secret YAML:\n{secret_yaml}")
                cmd = f"kubectl apply -f {temp_secret_file}"
                code, stdout, stderr = self.run_command(cmd)
                if code != 0:
                    print(f"Failed to create secret:")
                    print(f"  stdout: {stdout}")
                    print(f"  stderr: {stderr}")
                    print("Continuing deployment without TOS secret...")
                else:
                    print(f"Secret {secret_name} created: {stdout}")
                
                print(f"Creating PV {pv_name} for TOS...")
                pv_yaml = f"""apiVersion: v1
kind: PersistentVolume
metadata:
  name: {pv_name}
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  csi:
    driver: fsx.csi.volcengine.com
    volumeHandle: {pv_name}
    volumeAttributes:
      bucket: {tos_bucket}
      region: {tos_region}
      path: {tos_path}
      subpath: /
      type: TOS
      server: tos-{tos_region}.ivolces.com
      secretName: {secret_name}
      secretNamespace: {k8s_namespace}
"""
                temp_pv_file = "/tmp/vke_deploy_pv.yaml"
                with open(temp_pv_file, "w", encoding="utf-8") as f:
                    f.write(pv_yaml)
                print(f"PV YAML:\n{pv_yaml}")
                cmd = f"kubectl apply -f {temp_pv_file}"
                code, stdout, stderr = self.run_command(cmd)
                if code != 0:
                    print(f"Failed to create PV:")
                    print(f"  stdout: {stdout}")
                    print(f"  stderr: {stderr}")
                    print("Continuing deployment without TOS PV...")
                else:
                    print(f"PV {pv_name} created: {stdout}")
            
            # Check if PVC exists
            pvc_exists = self.check_pvc_exists(k8s_namespace, pvc_name)
            if not pvc_exists:
                print(f"Creating PVC {pvc_name} for TOS...")
                pvc_yaml = f"""apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {pvc_name}
  namespace: {k8s_namespace}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: 10Gi
  storageClassName: ""
  volumeName: {pv_name}
"""
                temp_pvc_file = "/tmp/vke_deploy_pvc.yaml"
                with open(temp_pvc_file, "w", encoding="utf-8") as f:
                    f.write(pvc_yaml)
                print(f"PVC YAML:\n{pvc_yaml}")
                cmd = f"kubectl apply -f {temp_pvc_file}"
                code, stdout, stderr = self.run_command(cmd)
                if code != 0:
                    print(f"Failed to create PVC:")
                    print(f"  stdout: {stdout}")
                    print(f"  stderr: {stderr}")
                    print("Continuing deployment without TOS PVC...")
                else:
                    print(f"PVC {pvc_name} created: {stdout}")
        
        if pvc_exists:
            print("PVC vikingbot-data 已存在，跳过PVC部署以避免修改不可变字段")
            resources = manifest_content.split("---")
            filtered_resources = []
            for res in resources:
                res = res.strip()
                if not res:
                    continue
                if "kind: PersistentVolumeClaim" in res:
                    continue
                filtered_resources.append(res)
            filtered_manifest = "/tmp/vke_deploy_filtered.yaml"
            with open(filtered_manifest, 'w', encoding='utf-8') as f:
                f.write("\n---\n".join(filtered_resources))
            deploy_path = filtered_manifest

        cmd = f"kubectl apply -f {deploy_path} -n {k8s_namespace}"
        code, stdout, stderr = self.run_command(cmd)

        if code != 0:
            print(f"部署失败: {stderr}")
            return False

        print(f"部署成功:\n{stdout}")

        # 如果跳过了镜像构建，触发滚动重启以强制重新拉取镜像
        if self.config.get("skip_build", False):
            print("\n检测到跳过镜像构建，触发滚动重启以强制重新拉取镜像...")
            deployment_name = self.config.get("k8s_deployment_name", "vikingbot")
            restart_cmd = f"kubectl rollout restart deployment/{deployment_name} -n {k8s_namespace}"
            restart_code, restart_stdout, restart_stderr = self.run_command(restart_cmd)
            if restart_code == 0:
                print(f"滚动重启已触发: {restart_stdout}")
            else:
                print(f"滚动重启触发失败: {restart_stderr}")

        if self.config.get("wait_for_rollout", True):
            self.wait_for_rollout(k8s_namespace)

        return True

    def print_deployment_diagnostics(self, namespace: str, deployment_name: str):
        print("\n=== 部署诊断信息 ===")

        print("\n1. Pod状态:")
        cmd = f"kubectl get pods -n {namespace} -l app={deployment_name}"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout:
            print(stdout)
        else:
            print(f"获取Pod状态失败: {stderr}")

        print("\n2. Pod事件:")
        cmd = f"kubectl get events -n {namespace} --sort-by='.lastTimestamp' | tail -20"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout:
            print(stdout)
        else:
            print(f"获取事件失败: {stderr}")

        print("\n3. Deployment详情:")
        cmd = f"kubectl describe deployment/{deployment_name} -n {namespace}"
        code, stdout, stderr = self.run_command(cmd)
        if code == 0 and stdout:
            print(stdout)
        else:
            print(f"获取Deployment详情失败: {stderr}")

        pods_cmd = f"kubectl get pods -n {namespace} -l app={deployment_name} -o name"
        code, pods_out, _ = self.run_command(pods_cmd)
        if code == 0 and pods_out:
            pod_name = pods_out.strip().split('\n')[0].replace('pod/', '')
            print(f"\n4. Pod日志 ({pod_name}):")
            log_cmd = f"kubectl logs {pod_name} -n {namespace} --tail=50"
            code, log_out, log_err = self.run_command(log_cmd)
            if code == 0 and log_out:
                print(log_out)
            elif log_err:
                print(log_err)

    def wait_for_rollout(self, namespace: str):
        print("\n=== 等待部署完成 ===")

        deployment_name = self.config.get("k8s_deployment_name", "vikingbot")

        timeout = self.config.get("rollout_timeout", 300)
        start_time = time.time()

        while time.time() - start_time < timeout:
            cmd = f"kubectl rollout status deployment/{deployment_name} -n {namespace} --timeout=30s"
            code, stdout, stderr = self.run_command(cmd)

            if code == 0:
                print("部署完成！")
                return

            print("等待中...")
            time.sleep(10)

        print("等待超时，正在收集诊断信息...")
        self.print_deployment_diagnostics(namespace, deployment_name)
        print("\n部署未完成，请根据上述信息排查问题。")

    def run(self):
        print("=" * 50)
        print("火山引擎VKE一键部署工具")
        print("=" * 50)

        if self.config.get("skip_build", False):
            print("跳过镜像构建")
        else:
            if not self.build_image():
                return False

        if self.config.get("skip_push", False):
            print("跳过镜像推送")
        else:
            if not self.login_registry():
                return False
            if not self.push_image():
                return False

        # 准备 OpenSandbox 镜像
        if self.config.get("opensandbox_enabled", True):
            if not self.prepare_opensandbox_image():
                return False

        kubeconfig_path = self.get_vke_kubeconfig()
        if not kubeconfig_path:
            return False

        # 创建 K8s 资源
        k8s_namespace = self.config.get("k8s_namespace", "default")
        if not self.create_k8s_resources(kubeconfig_path, k8s_namespace):
            return False

        if self.config.get("skip_deploy", False):
            print("跳过VKE部署")
        else:
            if not self.deploy_to_vke(kubeconfig_path):
                return False

        print("\n" + "=" * 50)
        print("🎉 部署流程完成！")
        print("=" * 50)
        return True


def main():
    parser = argparse.ArgumentParser(description="火山引擎VKE一键部署工具")
    parser.add_argument(
        "--config", "-c",
        help="配置文件路径 (默认: ~/.config/vikingbot/vke_deploy.yaml)"
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="跳过镜像构建"
    )
    parser.add_argument(
        "--skip-push",
        action="store_true",
        help="跳过镜像推送"
    )
    parser.add_argument(
        "--skip-deploy",
        action="store_true",
        help="跳过VKE部署"
    )
    parser.add_argument(
        "--skip-image-check",
        action="store_true",
        help="跳过镜像存在检查，直接构建"
    )
    parser.add_argument(
        "--image-tag",
        help="覆盖配置中的镜像tag"
    )

    args = parser.parse_args()

    deployer = VKEDeployer(args.config)

    if args.skip_build:
        deployer.config["skip_build"] = True
    if args.skip_push:
        deployer.config["skip_push"] = True
    if args.skip_deploy:
        deployer.config["skip_deploy"] = True
    if args.skip_image_check:
        deployer.config["skip_image_check"] = True
    if args.image_tag:
        deployer.config["image_tag"] = args.image_tag

    success = deployer.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

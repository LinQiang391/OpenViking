#!/usr/bin/env python3

import argparse
import base64
import os
import sys
import subprocess
from typing import Optional, Dict, Any

HAS_YAML = False
try:
    import yaml
    HAS_YAML = True
except ImportError:
    pass

class VKEDeployer:
    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self.load_config(config_path)

    def load_config(self, config_path: str) -> Dict[str, Any]:
        if not os.path.exists(config_path):
            print(f"Config file not found: {config_path}")
            sys.exit(1)

        with open(config_path, "r") as f:
            if HAS_YAML:
                config = yaml.safe_load(f)
            else:
                import shlex
                config = {}
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        try:
                            key, value = shlex.split(line, ":", 1)
                            config[key.strip()] = value.strip()
                        except:
                            pass
        return config

    def validate_config(self):
        required_fields = [
            "volcengine_access_key",
            "volcengine_secret_key",
            "vke_cluster_id",
        ]

        missing_fields = []
        for field in required_fields:
            value = self.config.get(field)
            if not value or value.startswith("AKLTxxxx") or value == "xxxxxx":
                missing_fields.append(field)

        if missing_fields:
            print("\nConfig validation failed! Missing or not updated fields:")
            for field in missing_fields:
                print(f"  - {field}")
            print("\nPlease edit config file and fill in correct values.")
            return False

        return True

    def run_command(self, cmd: str) -> tuple[int, str, str]:
        print(f"Executing command: {cmd}")
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        return result.returncode, result.stdout, result.stderr

    def build_image(self) -> bool:
        print("\n=== Step 1: Build Docker image ===")

        dockerfile_path = self.config.get("dockerfile_path", "deploy/Dockerfile")
        build_context = self.config.get("build_context", ".")
        local_image_name = self.config.get("local_image_name", "vikingbot")

        if not os.path.exists(dockerfile_path):
            print(f"Dockerfile not found: {dockerfile_path}")
            return False

        cmd = f"docker build --platform linux/amd64 -f {dockerfile_path} -t {local_image_name} {build_context}"
        code, stdout, stderr = self.run_command(cmd)

        if code != 0:
            print(f"Build image failed: {stderr}")
            return False

        print(f"Image build success: {local_image_name}")
        return True

    def push_image(self) -> bool:
        print("\n=== Step 2: Push image to registry ===")

        registry = self.config["image_registry"]
        namespace = self.config.get("image_namespace", "vikingbot")
        repository = self.config["image_repository"]
        local_image_name = self.config.get("local_image_name", "vikingbot")

        use_timestamp_tag = self.config.get("use_timestamp_tag", False)
        image_tag = self.config.get("image_tag", "latest")

        if use_timestamp_tag:
            from datetime import datetime
            image_tag = f"build-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

        full_image_name = f"{registry}/{namespace}/{repository}:{image_tag}"

        username = self.config.get("registry_username", "")
        password = self.config.get("registry_password", "")

        if username and password:
            login_cmd = f"docker login {registry} -u {username} -p {password}"
            code, stdout, stderr = self.run_command(login_cmd)
            if code != 0:
                print(f"Registry login failed: {stderr}")
                return False

        tag_cmd = f"docker tag {local_image_name} {full_image_name}"
        code, stdout, stderr = self.run_command(tag_cmd)
        if code != 0:
            print(f"Tag image failed: {stderr}")
            return False

        push_cmd = f"docker push {full_image_name}"
        code, stdout, stderr = self.run_command(push_cmd)
        if code != 0:
            print(f"Push image failed: {stderr}")
            return False

        print(f"Image push success: {full_image_name}")
        self.config["full_image_name"] = full_image_name
        return True

    def deploy_to_k8s(self) -> bool:
        print("\n=== Step 3: Deploy to Kubernetes ===")

        k8s_manifest_path = self.config.get("k8s_manifest_path", "deploy/vke/k8s/deployment.yaml")
        k8s_namespace = self.config.get("k8s_namespace", "default")
        k8s_deployment_name = self.config.get("k8s_deployment_name", "vikingbot")
        k8s_replicas = self.config.get("k8s_replicas", 1)
        kubeconfig_path = os.path.expanduser(self.config.get("kubeconfig_path", "~/.kube/config"))

        full_image_name = self.config.get("full_image_name")
        if not full_image_name:
            registry = self.config["image_registry"]
            namespace = self.config.get("image_namespace", "vikingbot")
            repository = self.config["image_repository"]
            image_tag = self.config["image_tag"]
            full_image_name = f"{registry}/{namespace}/{repository}:{image_tag}"

        if not os.path.exists(k8s_manifest_path):
            print(f"K8s manifest file not found: {k8s_manifest_path}")
            return False

        with open(k8s_manifest_path, "r") as f:
            manifest_content = f.read()

        if "__IMAGE_NAME__" in manifest_content:
            manifest_content = manifest_content.replace("__IMAGE_NAME__", full_image_name)
            print(f"Set image to: {full_image_name}")

        if "__REPLICAS__" in manifest_content:
            manifest_content = manifest_content.replace("__REPLICAS__", str(k8s_replicas))
            print(f"Set replicas to: {k8s_replicas}")

        storage_type = self.config.get("storage_type", "local")
        if storage_type == "tos":
            tos_bucket = self.config.get("tos_bucket", "vikingbot_data")
            tos_path = self.config.get("tos_path", "/.vikingbot/")
            tos_region = self.config.get("tos_region", "cn")

            volcengine_access_key = self.config.get("volcengine_access_key")
            volcengine_secret_key = self.config.get("volcengine_secret_key")

            access_key_b64 = base64.b64encode(volcengine_access_key.encode()).decode()
            secret_key_b64 = base64.b64encode(volcengine_secret_key.encode()).decode()

            secret_config = f"""apiVersion: v1
kind: Secret
metadata:
  name: vikingbot-tos-secret
  namespace: {k8s_namespace}
type: Opaque
data:
  AccessKeyId: {access_key_b64}
  SecretAccessKey: {secret_key_b64}
---
"""

            tos_pv_config = f"""apiVersion: v1
kind: PersistentVolume
metadata:
  name: vikingbot-tos-pv
spec:
  capacity:
    storage: 10Gi
  accessModes:
    - ReadWriteMany
  persistentVolumeReclaimPolicy: Retain
  storageClassName: ""
  csi:
    driver: fsx.csi.volcengine.com
    volumeHandle: vikingbot-tos-pv
    volumeAttributes:
      bucket: {tos_bucket}
      path: {tos_path}
      region: {tos_region}
      server: tos-{tos_region}.ivolces.com
      secretName: vikingbot-tos-secret
      secretNamespace: {k8s_namespace}
      type: TOS
---
"""

            tos_pv_config = secret_config + tos_pv_config

            manifest_content = tos_pv_config + manifest_content
            manifest_content = manifest_content.replace("__ACCESS_MODES__", "ReadWriteMany")
            manifest_content = manifest_content.replace("__STORAGE_CLASS_CONFIG__", "")
            manifest_content = manifest_content.replace("__VOLUME_NAME_CONFIG__", "volumeName: vikingbot-tos-pv")
            print(f"Set TOS config: bucket={tos_bucket}, path={tos_path}, region={tos_region}")
        else:
            manifest_content = manifest_content.replace("__ACCESS_MODES__", "ReadWriteOnce")
            manifest_content = manifest_content.replace("__STORAGE_CLASS_CONFIG__", "storageClassName: csi-ebs-ssd-default")
            manifest_content = manifest_content.replace("__VOLUME_NAME_CONFIG__", "")
            print("Set local EBS storage config")

        temp_manifest = "/tmp/vikingbot-deployment.yaml"
        with open(temp_manifest, "w") as f:
            f.write(manifest_content)

        print(f"Apply K8s manifest: {k8s_manifest_path}")
        cmd = f"kubectl apply -f {temp_manifest}"
        code, stdout, stderr = self.run_command(cmd)
        if code != 0:
            print(f"Apply K8s manifest failed: {stderr}")
            return False

        print(f"K8s resources applied successfully")

        if self.config.get("wait_for_rollout", True):
            rollout_timeout = self.config.get("rollout_timeout", 120)
            print(f"\nWaiting for deployment to complete (timeout: {rollout_timeout}s)...")
            cmd = f"kubectl rollout status deployment/{k8s_deployment_name} -n {k8s_namespace} --timeout={rollout_timeout}s"
            code, stdout, stderr = self.run_command(cmd)
            if code != 0:
                print(f"Deployment timeout or failed: {stderr}")
                print("Please check pod status: kubectl get pods -n {k8s_namespace}")
                return False

            print(f"Deployment success!")

        return True

    def run(self, skip_build: bool = False, skip_push: bool = False, skip_deploy: bool = False):
        print("==================================================")
        print("Volcengine VKE One-Click Deployment Tool")
        print("==================================================")
        print(f"Using config file: {self.config_path}")

        if not self.validate_config():
            return

        print("\nCurrent config summary:")
        print(f"  Region: {self.config.get('volcengine_region', 'cn-beijing')}")
        print(f"  Cluster ID: {self.config.get('vke_cluster_id', 'N/A')}")
        print(f"  Image: {self.config['image_registry']}/{self.config.get('image_namespace', 'vikingbot')}/{self.config['image_repository']}:{self.config.get('image_tag', 'latest')}")
        print(f"  Timestamp tag: {'enabled' if self.config.get('use_timestamp_tag', False) else 'disabled'}")
        print(f"  Dockerfile: {self.config.get('dockerfile_path', 'deploy/Dockerfile')}")
        print(f"  K8s manifest: {self.config.get('k8s_manifest_path', 'deploy/vke/k8s/deployment.yaml')}")
        print(f"  Storage type: {self.config.get('storage_type', 'local')}")

        if not skip_build:
            if not self.build_image():
                return
        else:
            print("\n=== Skip image build ===")

        if not skip_push:
            if not self.push_image():
                return
            else:
                print("\n=== Skip image push ===")

        if not skip_deploy:
            if not self.deploy_to_k8s():
                return
            else:
                print("\n=== Skip K8s deploy ===")

        print("\nDeployment complete!")

def main():
    parser = argparse.ArgumentParser(description="Volcengine VKE One-Click Deployment Tool")
    parser.add_argument("--config", "-c", default="~/.config/vikingbot/vke_deploy.yaml",
                      help="Config file path (default: ~/.config/vikingbot/vke_deploy.yaml)")
    parser.add_argument("--skip-build", action="store_true",
                      help="Skip image build")
    parser.add_argument("--skip-push", action="store_true",
                      help="Skip image push")
    parser.add_argument("--skip-deploy", action="store_true",
                      help="Skip K8s deploy")
    parser.add_argument("--skip-image-check", action="store_true",
                      help="Skip image existence check, build directly")
    parser.add_argument("--image-tag", help="Override image tag in config")

    args = parser.parse_args()

    config_path = os.path.expanduser(args.config)
    deployer = VKEDeployer(config_path)

    if args.image_tag:
        deployer.config["image_tag"] = args.image_tag

    deployer.run(
        skip_build=args.skip_build,
        skip_push=args.skip_push,
        skip_deploy=args.skip_deploy
    )

if __name__ == "__main__":
    main()

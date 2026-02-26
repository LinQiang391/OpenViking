# VKE Deployment Scripts

## Overview

Two deployment scripts are available for deploying vikingbot to Volcengine Kubernetes Engine (VKE):

- **vke_deploy.py** - Simple deployment without OpenSandbox sidecar
- **vke_with_opensandbox_deploy.py** - Deployment with OpenSandbox sidecar container

## vke_deploy.py (Simple Deployment)

Deploys vikingbot to VKE with support for single or multi-pod deployments using shared storage.

### Storage Options

1. **Local Storage (default)** - Uses `ReadWriteOnce` EBS storage for single-pod deployments
2. **TOS (TOS Object Storage)** - Uses `ReadWriteMany` TOS CSI driver for multi-pod deployments

### Configuration

Edit `~/.config/vikingbot/vke_deploy.yaml`:

```yaml
volcengine_access_key: AKLTxxxxxxxxxx
volcengine_secret_key: xxxxxxxxxx
volcengine_region: cn-beijing
vke_cluster_id: ccxxxxxxxxxx

image_registry: vikingbot-cn-beijing.cr.volces.com
image_namespace: vikingbot
image_repository: vikingbot
image_tag: latest

# For multi-pod deployment, use TOS storage
storage_type: tos  # or 'local' for single-pod
tos_bucket: vikingbot_data
tos_path: /.vikingbot/
tos_region: cn-beijing

k8s_replicas: 3  # Set > 1 for multi-pod deployment
```

### Deployment Manifests

- `deployment-simple.yaml` - Single-pod deployment with local EBS storage
- `deployment-tos.yaml` - Multi-pod deployment with TOS shared storage

### Usage

```bash
# Deploy with default settings (single-pod, local storage)
python3 deploy/vke/vke_deploy.py

# Deploy with multi-pod, TOS shared storage
# Edit config to set storage_type=tos and k8s_replicas>1
python3 deploy/vke/vke_deploy.py

# Skip build/push steps
python3 deploy/vke/vke_deploy.py --skip-build --skip-push
```

## vke_with_opensandbox_deploy.py (OpenSandbox Deployment)

Deploys vikingbot with OpenSandbox sidecar container for sandboxed execution.

### Features

- OpenSandbox server sidecar container
- Supports local and TOS storage
- Includes health checks for both containers

### Configuration

Additional OpenSandbox-specific configuration:

```yaml
opensandbox_enabled: true
opensandbox_image: opensandbox/server:latest
opensandbox_source_image: opensandbox/server:latest
```

### Deployment Manifest

- `deployment.yaml` - Deployment with OpenSandbox sidecar container

### Usage

```bash
python3 deploy/vke/vke_with_opensandbox_deploy.py
```

## Multi-Pod Deployment with TOS

For multi-pod deployments (horizontal scaling), use TOS object storage:

1. Create a TOS bucket in Volcengine console
2. Set `storage_type: tos` in config
3. Set `k8s_replicas: N` (where N > 1)
4. Configure TOS bucket, path, and region

The TOS CSI driver provides `ReadWriteMany` access mode, allowing multiple pods to share the same configuration and session data.

## Notes

- Both scripts use the same configuration file format
- The `storage_type` config determines which manifest template is used
- ImagePullSecret is automatically created from Volcengine credentials
- ServiceAccount and RBAC resources are created for proper permissions

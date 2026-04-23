# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class LocalEncryptionProviderConfig(BaseModel):
    """Local file encryption provider configuration.

    Uses a local file to store the Root Key.
    Suitable for single-user or development environments.
    """

    key_file: str = Field(
        default="~/.openviking/master.key", description="Path to local root key file"
    )


class VaultEncryptionProviderConfig(BaseModel):
    """HashiCorp Vault encryption provider configuration.

    Uses HashiCorp Vault Transit Secrets Engine for key management.
    Suitable for enterprise environments requiring centralized key management.
    """

    address: Optional[str] = Field(default=None, description="HashiCorp Vault address")
    token: Optional[str] = Field(default=None, description="HashiCorp Vault token")
    mount_point: str = Field(
        default="transit", description="HashiCorp Vault transit secrets engine mount point"
    )
    key_name: str = Field(default="openviking-root", description="HashiCorp Vault key name")


class VolcengineKMSEncryptionProviderConfig(BaseModel):
    """Volcengine KMS encryption provider configuration.

    Uses Volcengine Key Management Service for key management.
    Suitable for production environments on Volcengine.
    """

    key_id: Optional[str] = Field(default=None, description="Volcengine KMS key ID")
    region: str = Field(default="cn-beijing", description="Volcengine KMS region")
    access_key: Optional[str] = Field(default=None, description="Volcengine access key ID")
    secret_key: Optional[str] = Field(default=None, description="Volcengine secret access key")


class CryptoEngineConfig(BaseModel):
    """Configuration for the pluggable crypto engine.

    Controls which low-level crypto backend is used for AES-256-GCM and HKDF
    operations.  ``"default"`` uses the ``cryptography`` Python library (statically
    linked OpenSSL).  ``"kae"`` uses ctypes to call the system-installed OpenSSL and
    loads the KAE hardware accelerator engine (supports OpenSSL 1.1.1 and 3.x).
    """

    type: str = Field(
        default="default",
        description="Crypto engine type: 'default' (cryptography lib) or 'kae' (system OpenSSL + KAE)",
    )

    engine_id: str = Field(
        default="kae",
        description="OpenSSL engine/provider identifier to load (only used when type='kae')",
    )


class EncryptionConfig(BaseModel):
    """Configuration for encryption module.

    Provides configuration for multi-tenant encryption functionality including:
    - Envelope encryption with AES-256-GCM
    - Multiple key providers (Local File, Vault, Volcengine KMS)
    - API Key hashing with Argon2id
    - Pluggable crypto engine (default / KAE hardware accelerator)

    Example configurations:
        # Local file provider with default engine
        {
            "enabled": true,
            "provider": "local",
            "local": {
                "key_file": "~/.openviking/master.key"
            }
        }

        # Local file provider with KAE engine
        {
            "enabled": true,
            "provider": "local",
            "engine": {
                "type": "kae",
                "engine_id": "kae"
            },
            "local": {
                "key_file": "~/.openviking/master.key"
            }
        }
    """

    enabled: bool = Field(default=False, description="Whether encryption is enabled")

    provider: str = Field(
        default="local",
        description="Key provider type: 'local', 'vault', 'volcengine_kms'",
    )

    engine: CryptoEngineConfig = Field(
        default_factory=CryptoEngineConfig,
        description="Crypto engine configuration (default or kae)",
    )

    local: LocalEncryptionProviderConfig = Field(
        default_factory=LocalEncryptionProviderConfig,
        description="Local provider configuration",
    )

    vault: VaultEncryptionProviderConfig = Field(
        default_factory=VaultEncryptionProviderConfig,
        description="Vault provider configuration",
    )

    volcengine_kms: VolcengineKMSEncryptionProviderConfig = Field(
        default_factory=VolcengineKMSEncryptionProviderConfig,
        description="Volcengine KMS provider configuration",
    )

    params: Dict[str, Any] = Field(
        default_factory=dict, description="Additional encryption-specific parameters"
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EncryptionConfig":
        """Create configuration from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            EncryptionConfig instance
        """
        return cls(**data)

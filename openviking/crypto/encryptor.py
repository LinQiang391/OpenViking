# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
File encryptor - envelope encryption implementation.

Implements Envelope Encryption pattern:
- Each file has independent random File Key
- File Key is encrypted with Account Key
- Account Key is derived from Root Key
"""

from __future__ import annotations

import importlib
import secrets
import struct
import time
from typing import TYPE_CHECKING, Any, Tuple

from openviking.crypto.exceptions import (
    AuthenticationFailedError,
    CorruptedCiphertextError,
    InvalidMagicError,
    KeyMismatchError,
)
from openviking_cli.utils.logger import get_logger

from openviking.crypto.engine import CipherSuite, suite_from_id, suite_params

if TYPE_CHECKING:
    from openviking.crypto.engine import CryptoEngine
    from openviking.crypto.providers import RootKeyProvider

logger = get_logger(__name__)

# Magic number: OpenViking Encryption v1
MAGIC = b"OVE1"
MAGIC_LENGTH = len(MAGIC)

VERSION_1 = 0x01  # legacy: always AES-256-GCM, no algorithm byte
VERSION_2 = 0x02  # adds 1-byte algorithm/suite id after provider type
CURRENT_VERSION = VERSION_2

PROVIDER_LOCAL = 0x01
PROVIDER_VAULT = 0x02
PROVIDER_VOLCENGINE = 0x03


def _record_encryption_metrics(
    *metrics: Tuple[str, dict[str, Any]],
    debug_message: str,
) -> None:
    """Emit encryption metrics without letting observability failures affect crypto flows."""
    try:
        encryption_module = importlib.import_module("openviking.metrics.datasources.encryption")
        datasource = encryption_module.EncryptionEventDataSource
        for metric_name, metric_kwargs in metrics:
            getattr(datasource, metric_name)(**metric_kwargs)
    except Exception:
        logger.debug(debug_message, exc_info=True)


class FileEncryptor:
    """File encryptor."""

    def __init__(
        self,
        provider: "RootKeyProvider",
        engine: CryptoEngine | None = None,
        suite: CipherSuite | None = None,
    ):
        """
        Initialize FileEncryptor.

        Args:
            provider: RootKeyProvider instance
            engine: Optional CryptoEngine; defaults to DefaultCryptoEngine
            suite: CipherSuite used for **new** encryptions.
                   Decryption always reads the suite from the envelope header.
        """
        from openviking.crypto.engine import DEFAULT_SUITE, CipherSuite as CS

        self.provider = provider
        if engine is None:
            from openviking.crypto.engine import DefaultCryptoEngine
            engine = DefaultCryptoEngine()
        self._engine = engine
        self._suite: CS = suite if suite is not None else DEFAULT_SUITE
        self._provider_type = self._detect_provider_type(provider)

    def _detect_provider_type(self, provider: "RootKeyProvider") -> int:
        """Detect Provider type."""
        from openviking.crypto.providers import (
            LocalFileProvider,
            VaultProvider,
            VolcengineKMSProvider,
        )

        if isinstance(provider, LocalFileProvider):
            return PROVIDER_LOCAL
        elif isinstance(provider, VaultProvider):
            return PROVIDER_VAULT
        elif isinstance(provider, VolcengineKMSProvider):
            return PROVIDER_VOLCENGINE
        else:
            raise ValueError(f"Unknown provider type: {type(provider)}")

    async def encrypt(self, account_id: str, plaintext: bytes) -> bytes:
        """
        Encrypt file content.

        Args:
            account_id: Account ID
            plaintext: Plaintext content

        Returns:
            Encrypted content (Envelope format)
        """
        start = time.perf_counter()
        _record_encryption_metrics(
            ("record_payload_size", {"operation": "encrypt", "size_bytes": len(plaintext)}),
            ("record_bytes", {"operation": "encrypt", "size_bytes": len(plaintext)}),
            debug_message="Failed to record encryption pre-encrypt metrics",
        )

        suite = self._suite
        sp = suite_params(suite)

        status = "ok"
        try:
            file_key = secrets.token_bytes(sp["key_len"])
            data_iv = secrets.token_bytes(sp["iv_len"])
            encrypted_content = await self._aead_encrypt(file_key, data_iv, plaintext, suite)
            encrypted_file_key, key_iv = await self.provider.encrypt_file_key(file_key, account_id, suite)
            return self._build_envelope(
                self._provider_type,
                suite,
                encrypted_file_key,
                key_iv,
                data_iv,
                encrypted_content,
            )
        except Exception:
            status = "error"
            raise
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_operation",
                    {
                        "operation": "encrypt",
                        "status": status,
                        "duration_seconds": elapsed,
                    },
                ),
                debug_message="Failed to record encryption operation metrics",
            )

    async def decrypt(self, account_id: str, ciphertext: bytes) -> bytes:
        """
        Decrypt file content.

        Args:
            account_id: Account ID
            ciphertext: Ciphertext content

        Returns:
            Decrypted plaintext content
        """
        if not ciphertext.startswith(MAGIC):
            return ciphertext

        if len(ciphertext) < MAGIC_LENGTH:
            raise InvalidMagicError("Ciphertext too short")

        try:
            (
                provider_type,
                suite,
                encrypted_file_key,
                key_iv,
                data_iv,
                encrypted_content,
            ) = self._parse_envelope(ciphertext)
        except Exception as e:
            raise CorruptedCiphertextError(f"Failed to parse envelope: {e}")

        start = time.perf_counter()
        status = "ok"
        try:
            file_key = await self.provider.decrypt_file_key(encrypted_file_key, key_iv, account_id, suite)
        except Exception as e:
            status = "error"
            raise KeyMismatchError(f"Failed to decrypt file key: {e}")

        try:
            plaintext = await self._aead_decrypt(file_key, data_iv, encrypted_content, suite)
            _record_encryption_metrics(
                ("record_payload_size", {"operation": "decrypt", "size_bytes": len(ciphertext)}),
                ("record_bytes", {"operation": "decrypt", "size_bytes": len(ciphertext)}),
                debug_message="Failed to record encryption pre-decrypt metrics",
            )
            return plaintext
        except AuthenticationFailedError as e:
            status = "error"
            _record_encryption_metrics(
                ("record_auth_failed", {}),
                debug_message="Failed to record encryption authentication failure metrics",
            )
            raise AuthenticationFailedError(f"Authentication failed: {e}")
        except Exception as e:
            status = "error"
            raise AuthenticationFailedError(f"Authentication failed: {e}")
        finally:
            elapsed = time.perf_counter() - start
            _record_encryption_metrics(
                (
                    "record_operation",
                    {
                        "operation": "decrypt",
                        "status": status,
                        "duration_seconds": elapsed,
                    },
                ),
                debug_message="Failed to record decryption operation metrics",
            )

    def _build_envelope(
        self,
        provider_type: int,
        suite: CipherSuite,
        encrypted_file_key: bytes,
        key_iv: bytes,
        data_iv: bytes,
        encrypted_content: bytes,
    ) -> bytes:
        """Build v2 Envelope with explicit algorithm byte.

        V2 layout (13 bytes header):
        - Magic (4B): b"OVE1"
        - Version (1B): 0x02
        - Provider Type (1B)
        - Algorithm / Suite (1B)
        - Encrypted File Key Length (2B, big-endian)
        - Key IV Length (2B, big-endian)
        - Data IV Length (2B, big-endian)
        - Encrypted File Key (variable)
        - Key IV (variable)
        - Data IV (variable)
        - Encrypted Content (variable)
        """
        efk_len = len(encrypted_file_key)
        kiv_len = len(key_iv)
        div_len = len(data_iv)

        header = struct.pack(
            "!4sBBBHHH",
            MAGIC,
            CURRENT_VERSION,
            provider_type,
            suite.value,
            efk_len,
            kiv_len,
            div_len,
        )
        return header + encrypted_file_key + key_iv + data_iv + encrypted_content

    def _parse_envelope(
        self, ciphertext: bytes,
    ) -> Tuple[int, CipherSuite, bytes, bytes, bytes, bytes]:
        """Parse v1 or v2 Envelope.

        Returns:
            (provider_type, suite, encrypted_file_key, key_iv, data_iv, encrypted_content)
        """
        _V1_HEADER = 12  # 4 + 1 + 1 + 2 + 2 + 2
        _V2_HEADER = 13  # 4 + 1 + 1 + 1 + 2 + 2 + 2

        if len(ciphertext) < _V1_HEADER:
            raise CorruptedCiphertextError("Envelope too short")

        # Peek at version byte (offset 4)
        version = ciphertext[4]

        if version == VERSION_1:
            (
                magic, ver, provider_type,
                efk_len, kiv_len, div_len,
            ) = struct.unpack("!4sBBHHH", ciphertext[:_V1_HEADER])

            if magic != MAGIC:
                raise InvalidMagicError(f"Invalid magic: {magic}")

            suite = CipherSuite.AES_256_GCM  # v1 is always AES-256-GCM
            offset = _V1_HEADER

        elif version == VERSION_2:
            if len(ciphertext) < _V2_HEADER:
                raise CorruptedCiphertextError("Envelope too short for v2 header")

            (
                magic, ver, provider_type, suite_id,
                efk_len, kiv_len, div_len,
            ) = struct.unpack("!4sBBBHHH", ciphertext[:_V2_HEADER])

            if magic != MAGIC:
                raise InvalidMagicError(f"Invalid magic: {magic}")

            suite = suite_from_id(suite_id)
            offset = _V2_HEADER

        else:
            raise CorruptedCiphertextError(f"Unsupported version: {version}")

        efk_end = offset + efk_len
        kiv_end = efk_end + kiv_len
        div_end = kiv_end + div_len

        if len(ciphertext) < div_end:
            raise CorruptedCiphertextError("Incomplete envelope")

        encrypted_file_key = ciphertext[offset:efk_end]
        key_iv = ciphertext[efk_end:kiv_end]
        data_iv = ciphertext[kiv_end:div_end]
        encrypted_content = ciphertext[div_end:]

        return provider_type, suite, encrypted_file_key, key_iv, data_iv, encrypted_content

    async def _aead_encrypt(self, key: bytes, iv: bytes, plaintext: bytes,
                            suite: CipherSuite) -> bytes:
        """AEAD encryption via the pluggable :class:`CryptoEngine`."""
        return self._engine.encrypt(key, iv, plaintext, suite)

    async def _aead_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes,
                            suite: CipherSuite) -> bytes:
        """AEAD decryption via the pluggable :class:`CryptoEngine`."""
        return self._engine.decrypt(key, iv, ciphertext, suite)

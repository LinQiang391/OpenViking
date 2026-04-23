# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Pluggable crypto engine abstraction and implementations.

Provides a unified interface for AES-256-GCM encryption/decryption and HKDF key
derivation, with two concrete backends:

- DefaultCryptoEngine : uses the ``cryptography`` library (statically-linked OpenSSL).
- KAECryptoEngine     : uses cffi to call the system-installed OpenSSL (libcrypto),
                        optionally loading the KAE hardware accelerator engine.
                        Supports both OpenSSL 1.1.1 and 3.x.
"""

from __future__ import annotations

import abc
import ctypes
import ctypes.util
import struct
from typing import Optional, Tuple

from openviking.crypto.exceptions import AuthenticationFailedError, ConfigError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

AES_256_GCM_KEY_LEN = 32
AES_256_GCM_IV_LEN = 12
AES_256_GCM_TAG_LEN = 16
HKDF_OUTPUT_LEN = 32


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class CryptoEngine(abc.ABC):
    """Abstract crypto engine that providers and the file encryptor delegate to."""

    @abc.abstractmethod
    def aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        """AES-256-GCM encrypt. Returns ciphertext || 16-byte auth tag."""

    @abc.abstractmethod
    def aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        """AES-256-GCM decrypt. *ciphertext* includes the trailing 16-byte auth tag."""

    @abc.abstractmethod
    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int = HKDF_OUTPUT_LEN) -> bytes:
        """HKDF-SHA-256 key derivation."""

    def name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Default engine – delegates to ``cryptography`` (existing behaviour)
# ---------------------------------------------------------------------------

class DefaultCryptoEngine(CryptoEngine):
    """Crypto engine backed by the ``cryptography`` Python library."""

    def aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(key)
        return aesgcm.encrypt(iv, plaintext, associated_data=None)

    def aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(key)
        try:
            return aesgcm.decrypt(iv, ciphertext, associated_data=None)
        except Exception as e:
            raise AuthenticationFailedError(f"Decryption failed: {e}")

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int = HKDF_OUTPUT_LEN) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        hkdf = HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info)
        return hkdf.derive(ikm)


# ---------------------------------------------------------------------------
# KAE engine – cffi / ctypes calls into system libcrypto
# ---------------------------------------------------------------------------

def _find_libcrypto() -> str:
    """Locate ``libcrypto`` on the system."""
    for name in ("crypto", "libcrypto.so.3", "libcrypto.so.1.1"):
        path = ctypes.util.find_library(name)
        if path:
            return path
    for path in (
        "/usr/lib64/libcrypto.so",
        "/usr/lib/libcrypto.so",
        "/usr/lib/aarch64-linux-gnu/libcrypto.so",
        "/usr/lib/x86_64-linux-gnu/libcrypto.so",
    ):
        try:
            ctypes.CDLL(path)
            return path
        except OSError:
            continue
    raise ConfigError(
        "Cannot locate system libcrypto. "
        "Install openssl-devel / libssl-dev to use the KAE engine."
    )


def _openssl_major_version(lib) -> int:
    """Return 1 or 3 depending on the loaded OpenSSL version."""
    try:
        ver = lib.OpenSSL_version_num()
        major = (ver >> 28) & 0xF
        return major if major in (1, 3) else 3
    except Exception:
        return 3


class KAECryptoEngine(CryptoEngine):
    """Crypto engine that calls system OpenSSL libcrypto via ctypes,
    optionally loading the KAE hardware acceleration engine.

    Supports:
    - OpenSSL 1.1.1: ENGINE API  (``ENGINE_by_id("kae")``)
    - OpenSSL 3.x  : OSSL_PROVIDER API (``OSSL_PROVIDER_load(NULL, "kae")``)
    """

    def __init__(self, engine_id: str = "kae"):
        self._engine_id = engine_id
        self._lib = ctypes.CDLL(_find_libcrypto())
        self._openssl_major = _openssl_major_version(self._lib)
        self._engine_ptr = None   # OpenSSL 1.1.1 ENGINE*
        self._provider_ptr = None  # OpenSSL 3.x OSSL_PROVIDER*

        self._setup_ctypes_signatures()
        self._load_kae()
        logger.info(
            "KAECryptoEngine initialised (OpenSSL %s.x, engine=%s)",
            self._openssl_major, self._engine_id,
        )

    # ---- ctypes setup ---------------------------------------------------

    def _setup_ctypes_signatures(self) -> None:  # noqa: C901
        lib = self._lib
        c_void_p = ctypes.c_void_p
        c_int = ctypes.c_int
        c_char_p = ctypes.c_char_p
        c_ulong = ctypes.c_ulong

        lib.EVP_CIPHER_CTX_new.restype = c_void_p
        lib.EVP_CIPHER_CTX_new.argtypes = []

        lib.EVP_CIPHER_CTX_free.restype = None
        lib.EVP_CIPHER_CTX_free.argtypes = [c_void_p]

        lib.EVP_aes_256_gcm.restype = c_void_p
        lib.EVP_aes_256_gcm.argtypes = []

        lib.EVP_EncryptInit_ex.restype = c_int
        lib.EVP_EncryptInit_ex.argtypes = [c_void_p, c_void_p, c_void_p, c_char_p, c_char_p]

        lib.EVP_EncryptUpdate.restype = c_int
        lib.EVP_EncryptUpdate.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int), c_char_p, c_int]

        lib.EVP_EncryptFinal_ex.restype = c_int
        lib.EVP_EncryptFinal_ex.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int)]

        lib.EVP_DecryptInit_ex.restype = c_int
        lib.EVP_DecryptInit_ex.argtypes = [c_void_p, c_void_p, c_void_p, c_char_p, c_char_p]

        lib.EVP_DecryptUpdate.restype = c_int
        lib.EVP_DecryptUpdate.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int), c_char_p, c_int]

        lib.EVP_DecryptFinal_ex.restype = c_int
        lib.EVP_DecryptFinal_ex.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_int)]

        lib.EVP_CIPHER_CTX_ctrl.restype = c_int
        lib.EVP_CIPHER_CTX_ctrl.argtypes = [c_void_p, c_int, c_int, c_void_p]

        lib.EVP_CIPHER_CTX_set_padding.restype = c_int
        lib.EVP_CIPHER_CTX_set_padding.argtypes = [c_void_p, c_int]

        # HKDF via EVP_PKEY
        lib.EVP_PKEY_CTX_new_id.restype = c_void_p
        lib.EVP_PKEY_CTX_new_id.argtypes = [c_int, c_void_p]

        lib.EVP_PKEY_derive_init.restype = c_int
        lib.EVP_PKEY_derive_init.argtypes = [c_void_p]

        lib.EVP_PKEY_CTX_ctrl.restype = c_int
        lib.EVP_PKEY_CTX_ctrl.argtypes = [c_void_p, c_int, c_int, c_int, c_int, c_void_p]

        lib.EVP_PKEY_CTX_set_hkdf_md.restype = c_int
        lib.EVP_PKEY_CTX_set_hkdf_md.argtypes = [c_void_p, c_void_p]

        lib.EVP_PKEY_derive.restype = c_int
        lib.EVP_PKEY_derive.argtypes = [c_void_p, c_char_p, ctypes.POINTER(ctypes.c_size_t)]

        lib.EVP_PKEY_CTX_free.restype = None
        lib.EVP_PKEY_CTX_free.argtypes = [c_void_p]

        lib.EVP_sha256.restype = c_void_p
        lib.EVP_sha256.argtypes = []

        lib.OpenSSL_version_num.restype = c_ulong
        lib.OpenSSL_version_num.argtypes = []

        # EVP_PKEY_CTX_hkdf_mode / set_hkdf_* helpers (macros → ctrl calls)
        # We use raw ctrl calls below; signatures defined above.

        # OpenSSL 1.1.1 ENGINE API
        if self._openssl_major == 1:
            lib.ENGINE_by_id.restype = c_void_p
            lib.ENGINE_by_id.argtypes = [c_char_p]
            lib.ENGINE_init.restype = c_int
            lib.ENGINE_init.argtypes = [c_void_p]
            lib.ENGINE_set_default.restype = c_int
            lib.ENGINE_set_default.argtypes = [c_void_p, c_int]
            lib.ENGINE_free.restype = c_int
            lib.ENGINE_free.argtypes = [c_void_p]

        # OpenSSL 3.x OSSL_PROVIDER API
        if self._openssl_major >= 3:
            lib.OSSL_PROVIDER_load.restype = c_void_p
            lib.OSSL_PROVIDER_load.argtypes = [c_void_p, c_char_p]

    # ---- KAE loading ----------------------------------------------------

    def _load_kae(self) -> None:
        lib = self._lib
        engine_id = self._engine_id.encode()

        if self._openssl_major == 1:
            self._engine_ptr = lib.ENGINE_by_id(engine_id)
            if not self._engine_ptr:
                raise ConfigError(
                    f"KAE engine '{self._engine_id}' not found. "
                    "Ensure libkae is installed and openssl engine is registered."
                )
            if not lib.ENGINE_init(self._engine_ptr):
                raise ConfigError(f"Failed to initialise KAE engine '{self._engine_id}'")
            ENGINE_METHOD_ALL = 0xFFFF
            lib.ENGINE_set_default(self._engine_ptr, ENGINE_METHOD_ALL)
            logger.info("Loaded KAE via OpenSSL 1.1.1 ENGINE API")
        else:
            self._provider_ptr = lib.OSSL_PROVIDER_load(None, engine_id)
            if not self._provider_ptr:
                raise ConfigError(
                    f"KAE provider '{self._engine_id}' not available. "
                    "Ensure kae provider is installed for OpenSSL 3.x."
                )
            logger.info("Loaded KAE via OpenSSL 3.x OSSL_PROVIDER API")

    # ---- AES-256-GCM ----------------------------------------------------

    _EVP_CTRL_GCM_SET_IVLEN = 0x9
    _EVP_CTRL_GCM_GET_TAG = 0x10
    _EVP_CTRL_GCM_SET_TAG = 0x11

    def aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        lib = self._lib
        ctx = lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise AuthenticationFailedError("EVP_CIPHER_CTX_new failed")
        try:
            cipher = lib.EVP_aes_256_gcm()
            engine_ptr = self._engine_ptr if self._openssl_major == 1 else None

            if not lib.EVP_EncryptInit_ex(ctx, cipher, engine_ptr, None, None):
                raise AuthenticationFailedError("EVP_EncryptInit_ex (cipher) failed")
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_SET_IVLEN, len(iv), None):
                raise AuthenticationFailedError("Set GCM IV length failed")
            if not lib.EVP_EncryptInit_ex(ctx, None, None, key, iv):
                raise AuthenticationFailedError("EVP_EncryptInit_ex (key/iv) failed")

            out_len = ctypes.c_int(0)
            out_buf = ctypes.create_string_buffer(len(plaintext) + AES_256_GCM_TAG_LEN)

            if not lib.EVP_EncryptUpdate(ctx, out_buf, ctypes.byref(out_len), plaintext, len(plaintext)):
                raise AuthenticationFailedError("EVP_EncryptUpdate failed")
            ct_len = out_len.value

            final_buf = ctypes.create_string_buffer(32)
            final_len = ctypes.c_int(0)
            if not lib.EVP_EncryptFinal_ex(ctx, final_buf, ctypes.byref(final_len)):
                raise AuthenticationFailedError("EVP_EncryptFinal_ex failed")
            ct_len += final_len.value

            tag_buf = ctypes.create_string_buffer(AES_256_GCM_TAG_LEN)
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_GET_TAG, AES_256_GCM_TAG_LEN, tag_buf):
                raise AuthenticationFailedError("Get GCM tag failed")

            return out_buf.raw[:ct_len] + tag_buf.raw
        finally:
            lib.EVP_CIPHER_CTX_free(ctx)

    def aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        if len(ciphertext) < AES_256_GCM_TAG_LEN:
            raise AuthenticationFailedError("Ciphertext too short for GCM tag")

        ct_body = ciphertext[:-AES_256_GCM_TAG_LEN]
        tag = ciphertext[-AES_256_GCM_TAG_LEN:]

        lib = self._lib
        ctx = lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise AuthenticationFailedError("EVP_CIPHER_CTX_new failed")
        try:
            cipher = lib.EVP_aes_256_gcm()
            engine_ptr = self._engine_ptr if self._openssl_major == 1 else None

            if not lib.EVP_DecryptInit_ex(ctx, cipher, engine_ptr, None, None):
                raise AuthenticationFailedError("EVP_DecryptInit_ex (cipher) failed")
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_SET_IVLEN, len(iv), None):
                raise AuthenticationFailedError("Set GCM IV length failed")
            if not lib.EVP_DecryptInit_ex(ctx, None, None, key, iv):
                raise AuthenticationFailedError("EVP_DecryptInit_ex (key/iv) failed")

            out_len = ctypes.c_int(0)
            out_buf = ctypes.create_string_buffer(len(ct_body) + 32)

            if not lib.EVP_DecryptUpdate(ctx, out_buf, ctypes.byref(out_len), ct_body, len(ct_body)):
                raise AuthenticationFailedError("EVP_DecryptUpdate failed")
            pt_len = out_len.value

            tag_buf = ctypes.create_string_buffer(tag)
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_SET_TAG, AES_256_GCM_TAG_LEN, tag_buf):
                raise AuthenticationFailedError("Set GCM tag failed")

            final_buf = ctypes.create_string_buffer(32)
            final_len = ctypes.c_int(0)
            rc = lib.EVP_DecryptFinal_ex(ctx, final_buf, ctypes.byref(final_len))
            if not rc:
                raise AuthenticationFailedError("GCM authentication failed")
            pt_len += final_len.value

            return out_buf.raw[:pt_len]
        finally:
            lib.EVP_CIPHER_CTX_free(ctx)

    # ---- HKDF -----------------------------------------------------------

    _EVP_PKEY_HKDF = 1036
    _EVP_PKEY_OP_DERIVE = 1 << 10
    _EVP_PKEY_CTRL_HKDF_MD = 0x1000 + 1
    _EVP_PKEY_CTRL_HKDF_SALT = 0x1000 + 2
    _EVP_PKEY_CTRL_HKDF_KEY = 0x1000 + 3
    _EVP_PKEY_CTRL_HKDF_INFO = 0x1000 + 4
    _EVP_PKEY_CTRL_HKDF_MODE = 0x1000 + 5
    _EVP_PKEY_HKDEF_MODE_EXTRACT_AND_EXPAND = 0

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int = HKDF_OUTPUT_LEN) -> bytes:
        lib = self._lib
        pctx = lib.EVP_PKEY_CTX_new_id(self._EVP_PKEY_HKDF, None)
        if not pctx:
            raise ConfigError("EVP_PKEY_CTX_new_id(HKDF) failed")
        try:
            if not lib.EVP_PKEY_derive_init(pctx):
                raise ConfigError("EVP_PKEY_derive_init failed")

            # Set mode to extract-and-expand
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_MODE, self._EVP_PKEY_HKDEF_MODE_EXTRACT_AND_EXPAND, None,
            )

            md = lib.EVP_sha256()
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_MD, 0, md,
            )

            # Salt
            salt_buf = ctypes.create_string_buffer(salt)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_SALT, len(salt), salt_buf,
            )

            # IKM (key material)
            ikm_buf = ctypes.create_string_buffer(ikm)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_KEY, len(ikm), ikm_buf,
            )

            # Info
            info_buf = ctypes.create_string_buffer(info)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_INFO, len(info), info_buf,
            )

            # Derive
            out_len = ctypes.c_size_t(length)
            out_buf = ctypes.create_string_buffer(length)
            if not lib.EVP_PKEY_derive(pctx, out_buf, ctypes.byref(out_len)):
                raise ConfigError("EVP_PKEY_derive (HKDF) failed")

            return out_buf.raw[:out_len.value]
        finally:
            lib.EVP_PKEY_CTX_free(pctx)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_ENGINES = {
    "default": DefaultCryptoEngine,
    "kae": KAECryptoEngine,
}


def create_crypto_engine(engine_type: str = "default", **kwargs) -> CryptoEngine:
    """Create a :class:`CryptoEngine` by name.

    ``engine_type`` is one of ``"default"`` or ``"kae"``.
    Extra *kwargs* are forwarded to the engine constructor (e.g.
    ``engine_id="uadk_engine"`` for KAE).
    """
    cls = _ENGINES.get(engine_type)
    if cls is None:
        raise ConfigError(
            f"Unknown crypto engine '{engine_type}'. "
            f"Supported: {', '.join(sorted(_ENGINES))}"
        )
    return cls(**kwargs)

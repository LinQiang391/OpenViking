# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Pluggable crypto engine abstraction and implementations.

Provides a unified interface for authenticated encryption and key derivation,
with support for multiple cipher suites and two concrete backends:

- DefaultCryptoEngine : uses the ``cryptography`` library (statically-linked OpenSSL).
- KAECryptoEngine     : uses ctypes to call the system-installed OpenSSL (libcrypto),
                        optionally loading the KAE hardware accelerator engine.
                        Supports both OpenSSL 1.1.1 and 3.x.

Cipher suites
-------------
- ``AES_256_GCM``  : AES-256-GCM  + HKDF-SHA-256  (default, backward-compatible)
- ``SM4_GCM``      : SM4-GCM      + HKDF-SM3      (国密)
"""

from __future__ import annotations

import abc
import ctypes
import ctypes.util
import enum
from typing import Optional

from openviking.crypto.exceptions import AuthenticationFailedError, ConfigError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Cipher suite enum
# ---------------------------------------------------------------------------

class CipherSuite(enum.Enum):
    """Supported cipher suites.

    Each suite bundles an AEAD cipher with a matching KDF hash algorithm.
    The ``value`` is stored in the envelope header to identify the suite used
    at encryption time so that decryption can pick the right algorithm
    regardless of the current system default.
    """
    AES_256_GCM = 0x01   # AES-256-GCM  + HKDF-SHA-256
    SM4_GCM     = 0x02   # SM4-128-GCM  + HKDF-SM3


# Per-suite constants
_SUITE_PARAMS: dict[CipherSuite, dict] = {
    CipherSuite.AES_256_GCM: {
        "key_len": 32,
        "iv_len": 12,
        "tag_len": 16,
        "kdf_output_len": 32,
        "label": "AES-256-GCM + HKDF-SHA-256",
    },
    CipherSuite.SM4_GCM: {
        "key_len": 16,
        "iv_len": 12,
        "tag_len": 16,
        "kdf_output_len": 16,
        "label": "SM4-GCM + HKDF-SM3",
    },
}

DEFAULT_SUITE = CipherSuite.AES_256_GCM

GCM_TAG_LEN = 16


def suite_from_id(suite_id: int) -> CipherSuite:
    """Resolve an envelope algorithm byte to a :class:`CipherSuite`."""
    for s in CipherSuite:
        if s.value == suite_id:
            return s
    raise ConfigError(f"Unknown cipher suite id: 0x{suite_id:02x}")


def suite_params(suite: CipherSuite) -> dict:
    return _SUITE_PARAMS[suite]


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class CryptoEngine(abc.ABC):
    """Abstract crypto engine that providers and the file encryptor delegate to.

    All methods accept an explicit *suite* parameter so callers can select the
    algorithm at call-time (e.g. when decrypting files written with a different
    suite than the current default).
    """

    @abc.abstractmethod
    def encrypt(self, key: bytes, iv: bytes, plaintext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        """AEAD encrypt.  Returns ciphertext || 16-byte auth tag."""

    @abc.abstractmethod
    def decrypt(self, key: bytes, iv: bytes, ciphertext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        """AEAD decrypt.  *ciphertext* includes the trailing 16-byte auth tag."""

    @abc.abstractmethod
    def kdf(self, ikm: bytes, salt: bytes, info: bytes,
            suite: CipherSuite = DEFAULT_SUITE, length: int | None = None) -> bytes:
        """Key derivation (HKDF with suite-appropriate hash).

        *length* defaults to the suite's ``kdf_output_len`` when ``None``.
        """

    # Convenience aliases for backward-compatible call sites
    def aes_gcm_encrypt(self, key: bytes, iv: bytes, plaintext: bytes) -> bytes:
        return self.encrypt(key, iv, plaintext, CipherSuite.AES_256_GCM)

    def aes_gcm_decrypt(self, key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
        return self.decrypt(key, iv, ciphertext, CipherSuite.AES_256_GCM)

    def hkdf_sha256(self, ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
        return self.kdf(ikm, salt, info, CipherSuite.AES_256_GCM, length)

    def name(self) -> str:
        return self.__class__.__name__


# ---------------------------------------------------------------------------
# Default engine – delegates to ``cryptography`` (existing behaviour)
# ---------------------------------------------------------------------------

class DefaultCryptoEngine(CryptoEngine):
    """Crypto engine backed by the ``cryptography`` Python library."""

    def encrypt(self, key: bytes, iv: bytes, plaintext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        cipher = self._get_aead(suite, key)
        return cipher.encrypt(iv, plaintext, associated_data=None)

    def decrypt(self, key: bytes, iv: bytes, ciphertext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        cipher = self._get_aead(suite, key)
        try:
            return cipher.decrypt(iv, ciphertext, associated_data=None)
        except Exception as e:
            raise AuthenticationFailedError(f"Decryption failed: {e}")

    def kdf(self, ikm: bytes, salt: bytes, info: bytes,
            suite: CipherSuite = DEFAULT_SUITE, length: int | None = None) -> bytes:
        if length is None:
            length = suite_params(suite)["kdf_output_len"]
        md = self._get_hash(suite)
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        hkdf = HKDF(algorithm=md, length=length, salt=salt, info=info)
        return hkdf.derive(ikm)

    # ---- helpers ---------------------------------------------------------

    @staticmethod
    def _get_aead(suite: CipherSuite, key: bytes):
        if suite == CipherSuite.AES_256_GCM:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(key)
        elif suite == CipherSuite.SM4_GCM:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            try:
                from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _  # noqa: F811
                from cryptography.hazmat.decrepit.ciphers.algorithms import SM4
            except ImportError:
                pass
            # cryptography >= 43 exposes SM4 in various ways. For GCM mode
            # we need to construct it manually via the low-level Cipher API.
            from cryptography.hazmat.primitives.ciphers import Cipher, modes
            try:
                from cryptography.hazmat.primitives.ciphers.algorithms import SM4 as SM4Algo
            except ImportError:
                try:
                    from cryptography.hazmat.decrepit.ciphers.algorithms import SM4 as SM4Algo
                except ImportError:
                    raise ConfigError(
                        "SM4 algorithm not available in the installed cryptography library. "
                        "Upgrade to cryptography >= 43 or use the KAE engine."
                    )
            return _SM4GCMWrapper(key)
        else:
            raise ConfigError(f"Unsupported cipher suite: {suite}")

    @staticmethod
    def _get_hash(suite: CipherSuite):
        from cryptography.hazmat.primitives import hashes
        if suite == CipherSuite.AES_256_GCM:
            return hashes.SHA256()
        elif suite == CipherSuite.SM4_GCM:
            return hashes.SM3()
        else:
            raise ConfigError(f"Unsupported KDF hash for suite: {suite}")


class _SM4GCMWrapper:
    """Adapter that gives SM4-GCM the same ``encrypt`` / ``decrypt`` API as
    ``AESGCM`` from the ``cryptography`` library.

    Uses the low-level ``Cipher`` API because the high-level AEAD classes
    do not expose SM4-GCM directly.
    """

    def __init__(self, key: bytes):
        self._key = key

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        try:
            from cryptography.hazmat.primitives.ciphers.algorithms import SM4
        except ImportError:
            from cryptography.hazmat.decrepit.ciphers.algorithms import SM4

        encryptor = Cipher(SM4(self._key), modes.GCM(nonce)).encryptor()
        if associated_data is not None:
            encryptor.authenticate_additional_data(associated_data)
        ct = encryptor.update(data) + encryptor.finalize()
        return ct + encryptor.tag

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        try:
            from cryptography.hazmat.primitives.ciphers.algorithms import SM4
        except ImportError:
            from cryptography.hazmat.decrepit.ciphers.algorithms import SM4

        if len(data) < GCM_TAG_LEN:
            raise AuthenticationFailedError("Ciphertext too short for GCM tag")
        ct_body = data[:-GCM_TAG_LEN]
        tag = data[-GCM_TAG_LEN:]

        decryptor = Cipher(SM4(self._key), modes.GCM(nonce, tag)).decryptor()
        if associated_data is not None:
            decryptor.authenticate_additional_data(associated_data)
        return decryptor.update(ct_body) + decryptor.finalize()


# ---------------------------------------------------------------------------
# KAE engine – ctypes calls into system libcrypto
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

        # SM4-GCM requires OpenSSL 3.x; function may not exist on 1.1.1
        try:
            lib.EVP_sm4_gcm.restype = c_void_p
            lib.EVP_sm4_gcm.argtypes = []
            self._has_sm4_gcm = True
        except AttributeError:
            self._has_sm4_gcm = False

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

        lib.EVP_PKEY_derive.restype = c_int
        lib.EVP_PKEY_derive.argtypes = [c_void_p, c_char_p, ctypes.POINTER(ctypes.c_size_t)]

        lib.EVP_PKEY_CTX_free.restype = None
        lib.EVP_PKEY_CTX_free.argtypes = [c_void_p]

        lib.EVP_sha256.restype = c_void_p
        lib.EVP_sha256.argtypes = []

        # SM3 may not exist on older builds
        try:
            lib.EVP_sm3.restype = c_void_p
            lib.EVP_sm3.argtypes = []
            self._has_sm3 = True
        except AttributeError:
            self._has_sm3 = False

        lib.OpenSSL_version_num.restype = c_ulong
        lib.OpenSSL_version_num.argtypes = []

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

    # ---- cipher selection -----------------------------------------------

    def _evp_cipher(self, suite: CipherSuite):
        lib = self._lib
        if suite == CipherSuite.AES_256_GCM:
            return lib.EVP_aes_256_gcm()
        elif suite == CipherSuite.SM4_GCM:
            if not self._has_sm4_gcm:
                raise ConfigError(
                    "SM4-GCM not available in the loaded libcrypto. "
                    "Requires OpenSSL 3.x with SM4 support."
                )
            return lib.EVP_sm4_gcm()
        else:
            raise ConfigError(f"Unsupported cipher suite for KAE engine: {suite}")

    def _evp_md(self, suite: CipherSuite):
        lib = self._lib
        if suite == CipherSuite.AES_256_GCM:
            return lib.EVP_sha256()
        elif suite == CipherSuite.SM4_GCM:
            if not self._has_sm3:
                raise ConfigError(
                    "SM3 hash not available in the loaded libcrypto. "
                    "Requires OpenSSL with SM3 support."
                )
            return lib.EVP_sm3()
        else:
            raise ConfigError(f"Unsupported KDF hash for suite: {suite}")

    # ---- AEAD encrypt/decrypt -------------------------------------------

    _EVP_CTRL_GCM_SET_IVLEN = 0x9
    _EVP_CTRL_GCM_GET_TAG = 0x10
    _EVP_CTRL_GCM_SET_TAG = 0x11

    def encrypt(self, key: bytes, iv: bytes, plaintext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        lib = self._lib
        ctx = lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise AuthenticationFailedError("EVP_CIPHER_CTX_new failed")
        try:
            cipher = self._evp_cipher(suite)
            engine_ptr = self._engine_ptr if self._openssl_major == 1 else None

            if not lib.EVP_EncryptInit_ex(ctx, cipher, engine_ptr, None, None):
                raise AuthenticationFailedError("EVP_EncryptInit_ex (cipher) failed")
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_SET_IVLEN, len(iv), None):
                raise AuthenticationFailedError("Set GCM IV length failed")
            if not lib.EVP_EncryptInit_ex(ctx, None, None, key, iv):
                raise AuthenticationFailedError("EVP_EncryptInit_ex (key/iv) failed")

            out_len = ctypes.c_int(0)
            out_buf = ctypes.create_string_buffer(len(plaintext) + GCM_TAG_LEN)

            if not lib.EVP_EncryptUpdate(ctx, out_buf, ctypes.byref(out_len), plaintext, len(plaintext)):
                raise AuthenticationFailedError("EVP_EncryptUpdate failed")
            ct_len = out_len.value

            final_buf = ctypes.create_string_buffer(32)
            final_len = ctypes.c_int(0)
            if not lib.EVP_EncryptFinal_ex(ctx, final_buf, ctypes.byref(final_len)):
                raise AuthenticationFailedError("EVP_EncryptFinal_ex failed")
            ct_len += final_len.value

            tag_buf = ctypes.create_string_buffer(GCM_TAG_LEN)
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_GET_TAG, GCM_TAG_LEN, tag_buf):
                raise AuthenticationFailedError("Get GCM tag failed")

            return out_buf.raw[:ct_len] + tag_buf.raw
        finally:
            lib.EVP_CIPHER_CTX_free(ctx)

    def decrypt(self, key: bytes, iv: bytes, ciphertext: bytes,
                suite: CipherSuite = DEFAULT_SUITE) -> bytes:
        if len(ciphertext) < GCM_TAG_LEN:
            raise AuthenticationFailedError("Ciphertext too short for GCM tag")

        ct_body = ciphertext[:-GCM_TAG_LEN]
        tag = ciphertext[-GCM_TAG_LEN:]

        lib = self._lib
        ctx = lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise AuthenticationFailedError("EVP_CIPHER_CTX_new failed")
        try:
            cipher = self._evp_cipher(suite)
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
            if not lib.EVP_CIPHER_CTX_ctrl(ctx, self._EVP_CTRL_GCM_SET_TAG, GCM_TAG_LEN, tag_buf):
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

    # ---- KDF ------------------------------------------------------------

    _EVP_PKEY_HKDF = 1036
    _EVP_PKEY_OP_DERIVE = 1 << 10
    _EVP_PKEY_CTRL_HKDF_MD = 0x1000 + 1
    _EVP_PKEY_CTRL_HKDF_SALT = 0x1000 + 2
    _EVP_PKEY_CTRL_HKDF_KEY = 0x1000 + 3
    _EVP_PKEY_CTRL_HKDF_INFO = 0x1000 + 4
    _EVP_PKEY_CTRL_HKDF_MODE = 0x1000 + 5
    _EVP_PKEY_HKDEF_MODE_EXTRACT_AND_EXPAND = 0

    def kdf(self, ikm: bytes, salt: bytes, info: bytes,
            suite: CipherSuite = DEFAULT_SUITE, length: int | None = None) -> bytes:
        if length is None:
            length = suite_params(suite)["kdf_output_len"]
        lib = self._lib
        pctx = lib.EVP_PKEY_CTX_new_id(self._EVP_PKEY_HKDF, None)
        if not pctx:
            raise ConfigError("EVP_PKEY_CTX_new_id(HKDF) failed")
        try:
            if not lib.EVP_PKEY_derive_init(pctx):
                raise ConfigError("EVP_PKEY_derive_init failed")

            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_MODE, self._EVP_PKEY_HKDEF_MODE_EXTRACT_AND_EXPAND, None,
            )

            md = self._evp_md(suite)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_MD, 0, md,
            )

            salt_buf = ctypes.create_string_buffer(salt)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_SALT, len(salt), salt_buf,
            )

            ikm_buf = ctypes.create_string_buffer(ikm)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_KEY, len(ikm), ikm_buf,
            )

            info_buf = ctypes.create_string_buffer(info)
            lib.EVP_PKEY_CTX_ctrl(
                pctx, -1, self._EVP_PKEY_OP_DERIVE,
                self._EVP_PKEY_CTRL_HKDF_INFO, len(info), info_buf,
            )

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

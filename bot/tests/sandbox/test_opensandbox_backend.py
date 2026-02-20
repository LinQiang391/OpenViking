"""Tests for OpenSandbox backend."""

import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.sandbox.backends.opensandbox import _is_kubernetes_env, OpenSandboxBackend
from vikingbot.sandbox.base import SandboxNotStartedError


def test_is_kubernetes_env_with_env_var():
    with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.0.0.1"}):
        assert _is_kubernetes_env() is True


def test_is_kubernetes_env_with_serviceaccount(tmp_path):
    sa_dir = tmp_path / "var" / "run" / "secrets" / "kubernetes.io" / "serviceaccount"
    sa_dir.mkdir(parents=True)
    
    with patch("vikingbot.sandbox.backends.opensandbox.Path", return_value=tmp_path / "var" / "run" / "secrets" / "kubernetes.io" / "serviceaccount"):
        assert _is_kubernetes_env() is True


def test_is_kubernetes_env_false():
    with patch.dict(os.environ, {}, clear=True):
        with patch("vikingbot.sandbox.backends.opensandbox.Path.exists", return_value=False):
            assert _is_kubernetes_env() is False


@pytest.mark.asyncio
async def test_opensandbox_backend_init_local():
    mock_config = MagicMock()
    mock_config.backends.opensandbox.server_url = "http://localhost:8080"
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=False):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
        assert backend._server_url == "http://localhost:8080"
        assert backend._is_vke is False


@pytest.mark.asyncio
async def test_opensandbox_backend_init_vke():
    mock_config = MagicMock()
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=True):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
        assert backend._server_url == "http://opensandbox-server:8080"
        assert backend._is_vke is True


@pytest.mark.asyncio
async def test_opensandbox_start_creates_sandbox():
    mock_config = MagicMock()
    mock_config.backends.opensandbox.server_url = "http://localhost:8080"
    mock_config.backends.opensandbox.default_image = "opensandbox/code-interpreter:v1"
    mock_config.backends.opensandbox.runtime.timeout = 300
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=False):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
    
        mock_sandbox = AsyncMock()
        mock_sandbox.commands = AsyncMock()
    
        with patch("vikingbot.sandbox.backends.opensandbox._start_opensandbox_server", return_value=None):
            with patch("vikingbot.sandbox.backends.opensandbox._wait_for_server", return_value=True):
                with patch("vikingbot.sandbox.backends.opensandbox.Sandbox") as mock_sandbox_cls:
                    mock_sandbox_cls.create = AsyncMock(return_value=mock_sandbox)
                    
                    await backend.start()
                    
                    assert backend._sandbox is mock_sandbox
                    mock_sandbox_cls.create.assert_called_once()


@pytest.mark.asyncio
async def test_opensandbox_execute_not_started():
    mock_config = MagicMock()
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=False):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
        
        with pytest.raises(SandboxNotStartedError):
            await backend.execute("echo hello")


@pytest.mark.asyncio
async def test_opensandbox_execute_pwd_vke():
    mock_config = MagicMock()
    mock_config.backends.opensandbox.tos.enabled = True
    mock_config.backends.opensandbox.tos.mount_path = "/tos"
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=True):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
        backend._sandbox = MagicMock()
        
        result = await backend.execute("pwd")
        assert result == "/tos"


@pytest.mark.asyncio
async def test_opensandbox_stop():
    mock_config = MagicMock()
    
    with patch("vikingbot.sandbox.backends.opensandbox._is_kubernetes_env", return_value=False):
        backend = OpenSandboxBackend(mock_config, "test_session", Path("/tmp/workspace"))
        
        mock_sandbox = AsyncMock()
        mock_sandbox.kill = AsyncMock()
        mock_sandbox.close = AsyncMock()
        backend._sandbox = mock_sandbox
        
        await backend.stop()
        
        mock_sandbox.kill.assert_called_once()
        mock_sandbox.close.assert_called_once()
        assert backend._sandbox is None

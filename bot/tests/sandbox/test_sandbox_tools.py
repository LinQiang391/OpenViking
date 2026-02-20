"""Tests for tools with sandbox enabled."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vikingbot.agent.tools.filesystem import (
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    ListDirTool,
)
from vikingbot.agent.tools.shell import ExecTool
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.sandbox.base import SandboxDisabledError


class MockBackend:
    """Mock sandbox backend for testing."""

    def __init__(self, config, session_key, workspace):
        self.config = config
        self._session_key = session_key
        self._workspace = workspace
        self._running = False

    async def start(self):
        self._running = True
        self._workspace.mkdir(parents=True, exist_ok=True)

    async def execute(self, command, timeout=60, **kwargs):
        return f"Mock executed: {command}"

    async def read_file(self, path: str) -> str:
        sandbox_path = self._workspace / path
        if sandbox_path.exists():
            return sandbox_path.read_text(encoding="utf-8")
        raise FileNotFoundError(f"File not found: {path}")

    async def write_file(self, path: str, content: str) -> None:
        sandbox_path = self._workspace / path
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox_path.write_text(content, encoding="utf-8")

    async def list_dir(self, path: str) -> list[tuple[str, bool]]:
        sandbox_path = self._workspace / path
        items = []
        if sandbox_path.exists() and sandbox_path.is_dir():
            for item in sandbox_path.iterdir():
                items.append((item.name, item.is_dir()))
        return items

    async def stop(self):
        self._running = False

    def is_running(self):
        return self._running

    @property
    def workspace(self):
        return self._workspace


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_source_workspace():
    """Create a temporary source workspace directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sandbox_manager(temp_workspace, temp_source_workspace):
    """Create a mock sandbox manager."""
    mock_config = MagicMock()
    mock_config.mode = "per-session"
    mock_config.backend = "mock"

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, temp_workspace, temp_source_workspace)
        yield manager

    with patch("vikingbot.sandbox.manager.get_backend", return_value=MockBackend):
        manager = SandboxManager(mock_config, temp_workspace, temp_source_workspace)
        yield manager


@pytest.fixture
async def sandbox_with_test_files(sandbox_manager, temp_workspace):
    """Create a sandbox with test files."""
    # Get or create the sandbox first
    sandbox = await sandbox_manager.get_sandbox("test_session")
    
    # Create test files in sandbox
    (sandbox.workspace / "test.txt").write_text("Hello from sandbox!")
    (sandbox.workspace / "subdir").mkdir()
    (sandbox.workspace / "subdir" / "nested.txt").write_text("Nested content")
    
    return sandbox


class TestReadFileToolWithSandbox:
    """Tests for ReadFileTool with sandbox enabled."""

    async def test_read_relative_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test reading a relative path within sandbox."""
        tool = ReadFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("test.txt")
        assert result == "Hello from sandbox!"

    async def test_read_absolute_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test reading an absolute path within sandbox (should map to sandbox workspace)."""
        tool = ReadFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("/test.txt")
        assert result == "Hello from sandbox!"

    async def test_read_nested_relative_path(self, sandbox_manager, sandbox_with_test_files):
        """Test reading a nested relative path."""
        tool = ReadFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("subdir/nested.txt")
        assert result == "Nested content"

    async def test_read_nested_absolute_path(self, sandbox_manager, sandbox_with_test_files):
        """Test reading a nested absolute path."""
        tool = ReadFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("/subdir/nested.txt")
        assert result == "Nested content"

    async def test_read_file_not_found_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test reading a non-existent file in sandbox."""
        tool = ReadFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("nonexistent.txt")
        assert "Error: File not found" in result

    async def test_read_sandbox_disabled_fallback(self, disabled_sandbox_manager, temp_workspace):
        """Test reading with sandbox disabled (should use main workspace)."""
        (temp_workspace / "main_test.txt").write_text("Hello from main!")
        
        tool = ReadFileTool(allowed_dir=temp_workspace, sandbox_manager=disabled_sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute(str(temp_workspace / "main_test.txt"))
        assert result == "Hello from main!"


class TestWriteFileToolWithSandbox:
    """Tests for WriteFileTool with sandbox enabled."""

    async def test_write_relative_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test writing to a relative path within sandbox."""
        tool = WriteFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        content = "New content"
        result = await tool.execute("new_file.txt", content)
        assert "Successfully wrote" in result
        
        written = sandbox_with_test_files.workspace / "new_file.txt"
        assert written.exists()
        assert written.read_text() == content

    async def test_write_absolute_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test writing to an absolute path within sandbox."""
        tool = WriteFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        content = "Absolute path content"
        result = await tool.execute("/absolute_file.txt", content)
        assert "Successfully wrote" in result
        
        written = sandbox_with_test_files.workspace / "absolute_file.txt"
        assert written.exists()
        assert written.read_text() == content

    async def test_write_nested_path_creates_dirs(self, sandbox_manager, sandbox_with_test_files):
        """Test writing to a nested path creates parent directories."""
        tool = WriteFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        content = "Deep content"
        result = await tool.execute("deep/sub/dir/file.txt", content)
        assert "Successfully wrote" in result
        
        written = sandbox_with_test_files.workspace / "deep" / "sub" / "dir" / "file.txt"
        assert written.exists()
        assert written.read_text() == content


class TestEditFileToolWithSandbox:
    """Tests for EditFileTool with sandbox enabled."""

    async def test_edit_relative_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test editing a relative path within sandbox."""
        tool = EditFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("test.txt", "Hello", "Goodbye")
        assert "Successfully edited" in result
        
        edited = sandbox_with_test_files.workspace / "test.txt"
        assert edited.read_text() == "Goodbye from sandbox!"

    async def test_edit_absolute_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test editing an absolute path within sandbox."""
        tool = EditFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("/test.txt", "Hello", "Goodbye")
        assert "Successfully edited" in result
        
        edited = sandbox_with_test_files.workspace / "test.txt"
        assert edited.read_text() == "Goodbye from sandbox!"

    async def test_edit_file_not_found(self, sandbox_manager, sandbox_with_test_files):
        """Test editing a non-existent file."""
        tool = EditFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("nonexistent.txt", "old", "new")
        assert "Error: File not found" in result

    async def test_edit_old_text_not_found(self, sandbox_manager, sandbox_with_test_files):
        """Test editing with old_text not present."""
        tool = EditFileTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("test.txt", "Not present", "Replace")
        assert "Error: old_text not found" in result


class TestListDirToolWithSandbox:
    """Tests for ListDirTool with sandbox enabled."""

    async def test_list_relative_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test listing a relative path within sandbox."""
        tool = ListDirTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute(".")
        assert "test.txt" in result
        assert "subdir" in result

    async def test_list_absolute_path_in_sandbox(self, sandbox_manager, sandbox_with_test_files):
        """Test listing an absolute path within sandbox."""
        tool = ListDirTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("/")
        assert "test.txt" in result
        assert "subdir" in result

    async def test_list_nested_directory(self, sandbox_manager, sandbox_with_test_files):
        """Test listing a nested directory."""
        tool = ListDirTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("subdir")
        assert "nested.txt" in result

    async def test_list_nonexistent_directory(self, sandbox_manager, sandbox_with_test_files):
        """Test listing a non-existent directory."""
        tool = ListDirTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("nonexistent_dir")
        assert "Error: Directory not found" in result


class TestExecToolWithSandbox:
    """Tests for ExecTool with sandbox enabled."""

    async def test_exec_command_in_sandbox(self, sandbox_manager):
        """Test executing a command in sandbox."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("echo hello")
        assert "Mock executed" in result

    async def test_exec_pwd_in_sandbox(self, sandbox_manager):
        """Test pwd command in sandbox returns /."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("pwd")
        assert result == "/"

    async def test_exec_ls_in_sandbox(self, sandbox_manager):
        """Test ls command in sandbox."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("ls -la")
        assert "Mock executed" in result

    async def test_exec_rm_r_in_sandbox_allowed(self, sandbox_manager):
        """Test rm -r command is allowed in sandbox (safety guards skipped)."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("rm -r subdir")
        # In sandbox mode, safety guards are skipped, so command is passed to sandbox
        assert "Mock executed" in result

    async def test_exec_rm_rf_in_sandbox_allowed(self, sandbox_manager):
        """Test rm -rf command is allowed in sandbox."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("rm -rf test.txt")
        assert "Mock executed" in result

    async def test_exec_sandbox_disabled_fallback(self, disabled_sandbox_manager):
        """Test executing with sandbox disabled (should use local execution)."""
        tool = ExecTool(sandbox_manager=disabled_sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute('echo "test"')
        # Should execute locally and return output
        assert "test" in result

    async def test_exec_rm_r_sandbox_disabled_blocked(self, disabled_sandbox_manager):
        """Test rm -r is blocked when sandbox is disabled."""
        tool = ExecTool(sandbox_manager=disabled_sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("rm -r subdir")
        # When sandbox disabled, safety guards should block it
        assert "Error: Command blocked by safety guard" in result

    async def test_exec_with_working_dir_in_sandbox(self, sandbox_manager):
        """Test executing with working_dir parameter in sandbox."""
        tool = ExecTool(sandbox_manager=sandbox_manager)
        tool.set_session_key("test_session")
        
        result = await tool.execute("echo test", working_dir="/some/dir")
        assert "Mock executed" in result

"""
OpenViking Mount 模块测试

测试 OpenViking 文件系统挂载功能，每个session直接在workspace下管理
"""

import sys
from pathlib import Path
import shutil

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from vikingbot.openviking_mount import (
    SessionOpenVikingManager,
    get_session_ov_manager,
    FUSE_AVAILABLE
)


@pytest.fixture
def temp_workspace(tmp_path):
    """临时工作区fixture"""
    workspace = tmp_path / "vikingbot_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture
def session_manager(temp_workspace):
    """会话管理器fixture"""
    manager = SessionOpenVikingManager(base_workspace=temp_workspace)
    yield manager
    manager.unmount_all()


class TestSessionOpenVikingManager:
    """测试会话OpenViking管理器"""
    
    def test_initialization(self, session_manager, temp_workspace):
        """测试初始化"""
        assert session_manager.base_workspace == temp_workspace
        assert temp_workspace.exists()
    
    def test_get_session_workspace(self, session_manager):
        """测试获取会话工作区路径"""
        session_key = "test:session_123"
        workspace = session_manager.get_session_workspace(session_key)
        
        assert workspace.name == "test_session_123"
        assert workspace.parent == session_manager.base_workspace
    
    def test_get_session_ov_data_path(self, session_manager):
        """测试获取OpenViking数据路径"""
        session_key = "test:session_123"
        ov_data_path = session_manager.get_session_ov_data_path(session_key)
        
        assert ov_data_path.name == ".ov_data"
        assert ov_data_path.parent.name == "test_session_123"
    
    def test_mount_for_session(self, session_manager):
        """测试为会话挂载"""
        session_key = "test:session_456"
        
        # 挂载
        success = session_manager.mount_for_session(session_key, use_fuse=False)
        assert success
        
        # 验证已挂载
        assert session_manager.is_mounted(session_key)
        
        # 验证工作区目录存在
        workspace = session_manager.get_session_workspace(session_key)
        assert workspace.exists()
        
        # 验证数据目录存在
        ov_data_path = session_manager.get_session_ov_data_path(session_key)
        assert ov_data_path.exists()
    
    def test_get_api_mount(self, session_manager):
        """测试获取API挂载对象"""
        session_key = "test:session_789"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 获取API挂载
        api_mount = session_manager.get_api_mount(session_key)
        assert api_mount is not None
        assert api_mount.config is not None
    
    def test_unmount_for_session(self, session_manager):
        """测试卸载会话"""
        session_key = "test:session_unmount"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        assert session_manager.is_mounted(session_key)
        
        # 卸载
        success = session_manager.unmount_for_session(session_key)
        assert success
        
        # 验证已卸载
        assert not session_manager.is_mounted(session_key)
    
    def test_delete_session_workspace(self, session_manager, temp_workspace):
        """测试删除会话工作区（同时清理挂载）"""
        session_key = "test:session_delete"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 验证工作区存在
        workspace = session_manager.get_session_workspace(session_key)
        assert workspace.exists()
        
        # 删除工作区
        success = session_manager.delete_session_workspace(session_key)
        assert success
        
        # 验证工作区已删除
        assert not workspace.exists()
        
        # 验证已卸载
        assert not session_manager.is_mounted(session_key)
    
    def test_workspace_exists_check(self, session_manager):
        """测试workspace存在性检查"""
        session_key = "test:exists_check"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 验证workspace存在
        assert session_manager.is_workspace_exists(session_key)
        
        # 卸载
        session_manager.unmount_for_session(session_key)
        
        # 即使卸载了，workspace目录应该还在
        workspace = session_manager.get_session_workspace(session_key)
        assert workspace.exists()
    
    def test_manual_delete_workspace_detection(self, session_manager, temp_workspace):
        """测试系统外手动删除workspace后的检测和清理"""
        session_key = "test:manual_delete"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 验证已挂载
        assert session_manager.is_mounted(session_key)
        
        # 模拟系统外手动删除workspace
        workspace = session_manager.get_session_workspace(session_key)
        assert workspace.exists()
        
        import shutil
        shutil.rmtree(workspace)
        assert not workspace.exists()
        
        # 检测到workspace不存在
        assert not session_manager.is_workspace_exists(session_key)
        
        # 清理孤立挂载
        cleaned = session_manager.cleanup_orphaned_mounts()
        assert cleaned == 1
        
        # 验证已卸载
        assert not session_manager.is_mounted(session_key)
    
    def test_get_api_mount_with_deleted_workspace(self, session_manager, temp_workspace):
        """测试获取API挂载时workspace已被删除的情况"""
        session_key = "test:api_mount_deleted"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 获取API挂载（第一次）
        api_mount1 = session_manager.get_api_mount(session_key)
        assert api_mount1 is not None
        
        # 模拟系统外手动删除workspace
        workspace = session_manager.get_session_workspace(session_key)
        import shutil
        shutil.rmtree(workspace)
        assert not workspace.exists()
        
        # 再次获取API挂载 - 应该检测到并清理
        api_mount2 = session_manager.get_api_mount(session_key)
        
        # 应该返回None，因为workspace已被删除并清理了挂载
        assert api_mount2 is None
        assert not session_manager.is_mounted(session_key)
    
    def test_cleanup_orphaned_mounts_multiple(self, session_manager, temp_workspace):
        """测试清理多个孤立挂载"""
        sessions = ["test:orphan_1", "test:orphan_2", "test:orphan_3"]
        
        # 挂载所有会话
        for session_key in sessions:
            session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 验证都已挂载
        for session_key in sessions:
            assert session_manager.is_mounted(session_key)
        
        # 删除前两个的workspace
        import shutil
        for session_key in sessions[:2]:
            workspace = session_manager.get_session_workspace(session_key)
            shutil.rmtree(workspace)
        
        # 清理孤立挂载
        cleaned = session_manager.cleanup_orphaned_mounts()
        assert cleaned == 2
        
        # 验证前两个已清理，第三个还在
        assert not session_manager.is_mounted(sessions[0])
        assert not session_manager.is_mounted(sessions[1])
        assert session_manager.is_mounted(sessions[2])
    
    def test_multiple_sessions(self, session_manager):
        """测试多会话同时挂载"""
        sessions = ["telegram:user_1", "discord:user_2", "cli:interactive"]
        
        # 挂载所有会话
        for session_key in sessions:
            success = session_manager.mount_for_session(session_key, use_fuse=False)
            assert success
        
        # 验证所有都已挂载
        for session_key in sessions:
            assert session_manager.is_mounted(session_key)
        
        # 验证工作区都存在
        for session_key in sessions:
            workspace = session_manager.get_session_workspace(session_key)
            assert workspace.exists()
        
        # 卸载所有
        session_manager.unmount_all()
        
        # 验证都已卸载
        for session_key in sessions:
            assert not session_manager.is_mounted(session_key)
    
    def test_global_manager(self, temp_workspace):
        """测试全局管理器单例"""
        manager1 = get_session_ov_manager(base_workspace=temp_workspace)
        manager2 = get_session_ov_manager()
        
        assert manager1 is manager2
        
        # 清理
        manager1.unmount_all()


class TestSessionWorkspaceStructure:
    """测试会话工作区结构"""
    
    def test_workspace_structure(self, session_manager):
        """测试工作区目录结构"""
        session_key = "test:structure"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        workspace = session_manager.get_session_workspace(session_key)
        ov_data_path = session_manager.get_session_ov_data_path(session_key)
        
        # 验证结构
        assert workspace.is_dir()
        assert ov_data_path.is_dir()
        assert ov_data_path.parent == workspace
        
        # 验证路径格式
        assert workspace.name == "test_structure"
        assert ov_data_path.name == ".ov_data"
    
    def test_session_workspace_is_mount_point(self, session_manager):
        """测试会话workspace本身就是挂载点"""
        session_key = "test:mount_point"
        
        # 挂载
        session_manager.mount_for_session(session_key, use_fuse=False)
        
        # 获取API挂载
        api_mount = session_manager.get_api_mount(session_key)
        
        # 验证mount_point就是workspace本身
        workspace = session_manager.get_session_workspace(session_key)
        assert api_mount.config.mount_point == workspace


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

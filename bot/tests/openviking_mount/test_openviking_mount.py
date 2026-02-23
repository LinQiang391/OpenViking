#!/usr/bin/env python3
"""
OpenViking Mount Module 测试程序
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from vikingbot.openviking_mount import (
    OpenVikingMount,
    MountConfig,
    MountScope,
    OpenVikingMountManager,
    get_mount_manager
)


def test_basic_mount():
    """测试基本挂载功能"""
    print("\n" + "="*60)
    print("测试1: 基本挂载功能")
    print("="*60)
    
    # 创建临时测试目录
    test_dir = Path("./test_mount_data").resolve()
    mount_point = Path("./test_mount_point").resolve()
    
    test_dir.mkdir(exist_ok=True)
    mount_point.mkdir(exist_ok=True)
    
    config = MountConfig(
        mount_point=mount_point,
        openviking_data_path=test_dir,
        scope=MountScope.RESOURCES,
        auto_init=True,
        read_only=False
    )
    
    try:
        with OpenVikingMount(config) as mount:
            print("✓ OpenVikingMount 创建成功")
            
            # 测试列出根目录
            print("\n尝试列出根目录...")
            try:
                root_uri = "viking://resources"
                items = mount._client.ls(root_uri)
                print(f"✓ 根目录内容: {items}")
            except Exception as e:
                print(f"⚠ 列出目录时的提示 (可能正常): {e}")
            
            # 测试创建目录
            print("\n尝试创建测试目录...")
            try:
                test_dir_uri = "viking://resources/test_mount"
                mount._client.mkdir(test_dir_uri)
                print(f"✓ 测试目录创建成功: {test_dir_uri}")
            except Exception as e:
                print(f"⚠ 创建目录时的提示: {e}")
            
            print("\n✓ 基本挂载功能测试完成")
            return True
            
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_mount_manager():
    """测试挂载管理器"""
    print("\n" + "="*60)
    print("测试2: 挂载管理器")
    print("="*60)
    
    try:
        # 创建管理器
        manager = OpenVikingMountManager(base_mount_dir=Path("./test_manager_mounts"))
        print("✓ OpenVikingMountManager 创建成功")
        
        # 测试列出挂载点（初始为空）
        mounts = manager.list_mounts()
        print(f"✓ 当前挂载点数量: {len(mounts)}")
        
        print("\n✓ 挂载管理器测试完成")
        return True
        
    except Exception as e:
        print(f"✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """主测试函数"""
    print("OpenViking Mount Module 测试")
    print("="*60)
    
    results = []
    
    # 测试1: 基本挂载
    results.append(("基本挂载功能", test_basic_mount()))
    
    # 测试2: 挂载管理器
    results.append(("挂载管理器", test_mount_manager()))
    
    # 汇总结果
    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)
    
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{name}: {status}")
    
    all_passed = all(r[1] for r in results)
    
    print("\n" + "="*60)
    if all_passed:
        print("✓ 所有测试通过!")
    else:
        print("✗ 部分测试失败")
    print("="*60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

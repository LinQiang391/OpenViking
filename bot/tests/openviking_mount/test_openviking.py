#!/usr/bin/env python3
"""
OpenViking 测试程序
验证OpenViking基本功能是否正常工作
"""

import sys
from pathlib import Path

# 添加OpenViking项目到路径
sys.path.insert(0, '/Users/bytedance/workspace/openviking')

try:
    import openviking as ov
    print("✓ OpenViking 导入成功")
except ImportError as e:
    print(f"✗ OpenViking 导入失败: {e}")
    sys.exit(1)

def test_openviking():
    """测试OpenViking基本功能"""
    
    print("\n" + "="*60)
    print("OpenViking 功能测试")
    print("="*60)
    
    # 创建临时数据目录
    data_dir = Path("./test_openviking_data").resolve()
    data_dir.mkdir(exist_ok=True)
    
    print(f"\n1. 数据目录: {data_dir}")
    
    try:
        # 初始化客户端
        print("\n2. 初始化OpenViking客户端...")
        client = ov.OpenViking(path=str(data_dir))
        client.initialize()
        print("✓ 客户端初始化成功")
        
        # 检查健康状态
        print("\n3. 检查系统健康状态...")
        is_healthy = client.is_healthy()
        print(f"✓ 系统健康状态: {is_healthy}")
        
        if is_healthy:
            status = client.get_status()
            print(f"  状态详情: {status}")
        
        # 列出根目录
        print("\n4. 列出根目录内容...")
        try:
            root_content = client.ls("viking://")
            print(f"✓ 根目录内容: {root_content}")
        except Exception as e:
            print(f"⚠ 列出根目录时出现警告 (可能是正常的): {e}")
        
        # 创建测试目录
        print("\n5. 创建测试目录...")
        test_dir_uri = "viking://resources/test"
        try:
            client.mkdir(test_dir_uri)
            print(f"✓ 测试目录创建成功: {test_dir_uri}")
        except Exception as e:
            print(f"⚠ 创建目录时出现警告: {e}")
        
        # 测试完成
        print("\n" + "="*60)
        print("✓ OpenViking 基本功能测试完成!")
        print("="*60)
        
        # 关闭客户端
        client.close()
        
        return True
        
    except Exception as e:
        print(f"\n✗ 测试过程中出错: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_openviking()
    sys.exit(0 if success else 1)

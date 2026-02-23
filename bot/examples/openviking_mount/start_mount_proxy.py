#!/usr/bin/env python3
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
os.chdir(project_root)

os.environ['FUSE_LIBRARY_PATH'] = '/usr/local/lib/libfuse-t.dylib'
sys.path.insert(0, str(project_root))

from vikingbot.openviking_mount.fuse_proxy import mount_fuse
from vikingbot.openviking_mount.mount import MountConfig, MountScope

def main():
    mount_point = project_root / "t1" / "ov"
    openviking_data_path = project_root / "t1_ov_data"
    
    print("="*60)
    print("OpenViking FUSE Proxy")
    print("="*60)
    print(f"Mount point: {mount_point}")
    print(f"Proxy to: {openviking_data_path / '.original_files'}")
    print(f"Data path: {openviking_data_path}")
    print("="*60)

    print("支持Finder拖放PDF！async模式已启用，复制更快！")
    print("Press Ctrl+C to unmount")
    print("="*60)
    
    mount_point.mkdir(parents=True, exist_ok=True)
    
    config = MountConfig(
        mount_point=mount_point,
        openviking_data_path=openviking_data_path,
        scope=MountScope.RESOURCES,
        auto_init=True,
        read_only=False,
        async_add_resource=True
    )
    
    try:
        mount_fuse(config, foreground=True)
    except KeyboardInterrupt:
        print("\nUnmounted")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()

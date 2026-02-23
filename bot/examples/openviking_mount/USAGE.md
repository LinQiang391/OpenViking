
# OpenViking FUSE Mount 使用说明

## 启动挂载

打开终端1，运行：
```bash
cd /Users/bytedance/workspace/openviking/bot
source .venv/bin/activate
python examples/openviking_mount/start_mount_simple.py
```

## 验证挂载

打开终端2，运行：
```bash
cd /Users/bytedance/workspace/openviking/bot
source .venv/bin/activate
python examples/openviking_mount/verify_mount.py
```

或者直接查看：
```bash
ls -la /Users/bytedance/workspace/openviking/bot/t1/ov/
```

## 预期结果

你应该看到：
- t1/ov 是挂载目录
- 里面有 Jina_AI_DeepSearch_线下沙龙分享_肖涵.pdf 文件
- 可以读取这个PDF文件的内容

## 停止挂载

在终端1按 Ctrl+C

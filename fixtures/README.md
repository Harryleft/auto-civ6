# 存档与历史数据

## 可复用存档

`fixtures/saves/` 仅保留用户确认的原始基准 `benchmark_turn_1.Civ6Save` 及其身份、种子和 SHA-256 清单。所有实机测试从该基准开始，原件不得覆盖。旧示例地图已移除，不再安装或加载。

```bash
uv run python scripts/install_saves.py --dry-run
uv run python scripts/install_saves.py
```

安装脚本默认只复制缺失存档；`--force` 才覆盖游戏目录中的同名文件。场景设置见 [存档说明](saves/README.md)。

## 历史日志

`fixtures/archive/` 保存原有 5 个 diary JSONL 和 4 个工具日志 JSONL，内容未修改。这些是旧网页演示数据，供离线分析参考；当前 MCP 无需读取它们，也无需运行数据加载服务。

# 存档与历史数据

## 可复用存档

`fixtures/saves/` 保留原有 3 个 `.Civ6Save` 文件：Ground Control、Snowflake、Cry Havoc。文件内容未修改，可用于明确指定存档的实机诊断。

```bash
uv run python scripts/install_saves.py --dry-run
uv run python scripts/install_saves.py
```

安装脚本默认只复制缺失存档；`--force` 才覆盖游戏目录中的同名文件。场景设置见 [存档说明](saves/README.md)。

## 历史日志

`fixtures/archive/` 保存原有 5 个 diary JSONL 和 4 个工具日志 JSONL，内容未修改。这些是旧网页演示数据，供离线分析参考；当前 MCP 无需读取它们，也无需运行数据加载服务。

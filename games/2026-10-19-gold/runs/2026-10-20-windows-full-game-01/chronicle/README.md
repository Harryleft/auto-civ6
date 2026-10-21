# 窄土之上，群星之间

苏美尔／吉尔伽美什神级原局的互动编年史。原局从第1回合推进至第243回合，最终取得原生外交胜利，保留四城66人口。

**在线阅读：[窄土之上，群星之间](https://empire-chronicle.dongchao.chatgpt.site)。**

## 文件

| 文件 | 用途 |
| --- | --- |
| [index.html](index.html) | 已发布网页的独立静态快照，可直接用浏览器打开；不需要连接游戏或Codex内联读取接口 |
| [empire-chronicle.html](empire-chronicle.html) | 可编辑的内联可视化源片段，保留CSS、交互代码和内嵌数据 |
| [data/chronicle.json](data/chronicle.json) | UTF-8结构化年表：十二卷、243回合、119条初见地形记录 |
| [data/timeline.csv](data/timeline.csv) | 可用于表格分析的逐回合年表，包含故事、观察、取舍、动作或尝试、原始日志行段 |
| [manifest.json](manifest.json) | 对局身份、公开站点、来源及归档文件SHA-256清单 |
| [../reports/ooda-review.md](../reports/ooda-review.md) | 逐回合OODA复盘入口，包含原始请求号与结果回读 |
| [../checkpoints/README.md](../checkpoints/README.md) | 可加载的本轮第1、241、242回合存档及过程指纹清单 |

这是静态历史资料，网页不会操作游戏、自动续玩、读取本机存档或同步游戏状态。独立网页与内联片段内嵌的年表应与JSON的对应数据一致，本次归档已逐项核对。

## 数据说明

- `chapters`：十二卷叙事，`a`、`b`为覆盖的起止回合。
- `days`：243条逐回合记录，`t`为回合；`observe`、`orient`、`commands`分别记录查询、取舍摘要与动作或尝试，`refs`对应原局JSONL行段。
- `tiles`：实际读取过的地形及首次记录回合；地图空白只表示当时日志未覆盖，不等于原生游戏迷雾。初见地形不复现此后的砍伐、改良或疆界变化。
- 普通回合若没有单独记下取舍，叙事明确沿用阶段目标；动作清单包含失败和未确认请求，不能全部当成已执行成功。
- 第242回合外交点数达到20，但原生获胜队伍仍为空；第243回合才正式确认外交胜利。飞船尚未抵达，不能写成科学胜利。
- 后续巨作修复的239—241回合是独立验证分支，不并入这份原局年表；另见[验收复盘](../reports/ooda/great-work-test-239-241.md)与[分支日志](../evidence/great-work-followup.jsonl)。

原始来源为[原局调用日志](../evidence/mcp-calls.jsonl)、[阶段进度](../reports/progress.md)和[终局证据](../reports/terminal-evidence.json)。来源日志、HTML快照和存档按原始字节保留；其余文本统一按LF行尾计算归档指纹，跨平台检出规则记录于仓库.gitattributes。

## 本地阅读

直接用浏览器打开index.html即可。也可以在此目录运行：

```bash
python -m http.server 8000
```

然后访问 `http://127.0.0.1:8000/`。CSV为UTF-8编码；表格软件导入时选择UTF-8。

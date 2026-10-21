# 本轮关键存档

本目录保存原局三份可加载的关键存档副本，并登记本机241份本轮过程存档的文件指纹。本次仅做文件复制与校验，没有加载存档或推进游戏。

| 存档 | 阶段 | SHA-256 |
| --- | --- | --- |
| [full01_20261020_t001_start.Civ6Save](full01_20261020_t001_start.Civ6Save) | 本轮第1回合起点；与受信原始基准是不同的保存文件 | `d0156059cb41b32cf678e7fda1fb1aaaca1757ae958dba719820e2a5cb8dfa73` |
| [full01_20261020_t241_start.Civ6Save](full01_20261020_t241_start.Civ6Save) | 原局终局前检查点；不是后续巨作测试结果 | `99c3cd09158558ecba8739f2ad9f45e2b4dbfbc1a8e927803242e2688de983dc` |
| [full01_20261020_t242_start.Civ6Save](full01_20261020_t242_start.Civ6Save) | 海洋家园完成、外交点数已达20，原生尚未确认获胜队伍 | `bea762801c76fd94ddeb2c3bee063d18b296d582c52c5cc0264be71996749038` |

原局第243回合外交胜利已由[终局证据](../reports/terminal-evidence.json)及[原始请求4526／4527](../evidence/mcp-calls.jsonl)确认，没有另外制造或命名一份243回合存档。

[manifest.json](manifest.json)记录上述副本的大小、SHA-256与完整本机过程存档清单。`included_in_git=false`表示该过程快照只收录文件指纹，二进制仍保留本机；不把它写成已上传。本机全部过程快照合计约213MB，归档选取其中三份关键副本。

## 查看存档

1. 安装Civilization VI及本局所用的风云变幻规则集，将所需副本复制到该电脑的 `Saves/Single/` 存档目录。
2. 在游戏原生加载菜单选择对应文件；如当前已有对局，先保留当前进度。
3. 通过MCP恢复时，仍按项目[检查点与恢复规则](../../../../../docs/agent-recovery.md)核对文件、轮次、文明、种子与回合，不能把文件名当作全部身份证据。

受信原始基准仍在[fixtures/saves](../../../../../fixtures/saves/README.md)，本目录不会替换它。后续巨作修复测试的241回合保存不属于上述三份原局副本。

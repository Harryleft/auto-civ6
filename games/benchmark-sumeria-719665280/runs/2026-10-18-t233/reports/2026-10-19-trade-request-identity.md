# 交易确认请求身份验收

[规格](../changes/2026-10-19-trade-request-identity.md)与[工作项](../changes/2026-10-19-trade-request-identity-tickets.md)。实施731b787，独立合入047a391，分支codex/native-pillage；未合main、未push。

T207向巴西试价时，run_trade仍持有旧瑞典交易的清理阶段，返回旧ACCEPTED与清理结果；1799–1802核对无会话、金币未减少、未获得化妆品。旧尝试在1798正常退役，1803之后才实际提出巴西报价，1805成交及1807资源回读独立确认。

修复在恢复旧尝试之前，对照目标、模式、报价和索取的准备副本；不匹配明确PENDING_TRADE_MISMATCH，零原生调用，保留尝试和门禁，不返回旧成功。原请求正常确认退役后，新请求仍走原路径。

| 验证 | 结果 |
| --- | --- |
| 离线 | trade_progress、diplomacy_cleanup两个直接相关模块97项通过；覆盖DIALOGUE／CLEANUP×四类身份变化、原请求续行、退役后新请求、输入副本及内存MCP；编译与差分检查通过 |
| 双轴审查 | Standards、Spec各0材料性问题 |
| 实机正常交易 | 同世界T208，1828／1829加拿大两次试价；1830出售一份多余丝绸，获得2金币及每回合3金币，原生成交确认 |
| 不同请求 | 1831保留成交对话期间将mode改为test，0.004秒明确拒绝，新请求未提交；1832仍显示原加拿大对话 |
| 原请求续行 | 1833按当前EXIT关闭；1834原样send请求只确认原成交并清理成功 |
| 保全 | 1824修复前T208存档1,266,498字节，31个原生文件归档；未重载游戏、未替换原有存档和验收 |

证据位于~/.civ6-mcp/acceptance/20261018-turn-terminal121/的trade-request-impl、trade-request-merge、trade-request-review与live/1792–1834。没有故意制造未知结果或追加隐式交易。

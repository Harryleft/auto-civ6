# 20261030 四阶段购买报价验收

`city production` 现在分别显示金币／信仰价格、余额、购买资格和拒绝原因；生产与购买候选取并集。指定物品核价入口为 `city production ID --item-type UNIT|BUILDING --item NAME`，也可读取当前不可购买的物品。实际购买前重新检查，继续以新产物及精确扣款确认完成。

| 验证项 | 结果 |
| --- | --- |
| 四阶段实机 | T50／T100／T150／T200；7 次成功购买的报价与扣款全部一致 |
| 拒绝验证 | 4 次实际拒绝；双币余额账对齐成功扣款，无额外扣款 |
| 解释能力 | 币种未启用、余额不足、缺前置建筑、已有建筑、单位占位、商人容量、缺战略资源 |
| 离线 | 851 项相关集成回归通过；Lua 5.1、GameState、MCP、命名 CLI 及未决门禁覆盖 |
| 来源保护 | 四份历史文件及登记字节指纹未变；八份本轮起点／结果另存 |
| 中断与恢复 | 中断前最后一次 pending 全 false；恢复时无游戏／CLI 进程，未重放购买 |

来源为[四叶草信仰对局](../../../2026-10-25-clover-faith/README.md)，原轮次 `3f9996c1b4734780b0aa1e581bde1fd7`；原始基准 `benchmark_gold_tun_1.Civ6Save` 的 SHA-256 为 `0d25d557e68b0efbd1aed7e66de9b8434f3a9ac9d321291d45cbe9df83fe3abf`。

见[验证报告](reports/validation.md)、[结构化结果](reports/purchase-results.json)和实测前形成的[验收约定](changes/acceptance-plan.md)。各阶段净推进 0 回合；本轮验证购买事实与拒绝解释，不将新单位、建筑或筹备投入当作后续战略收益已经兑现。

原始证据保留在本地 `evidence/`，不纳入 Git：

- `live-cli`：首轮实测揭示 `CanStartCommand` 缺少币种门槛，尚未购买。
- `live-fixed`：补齐原生 UI 币种门槛后的只读复核，尚未购买。
- `live-final`：T50／T100／T150 的最终报价、实际购买、拒绝与保存。
- `live-t200`：最终实现的 T200 合并查询、报价、购买、拒绝与保存。
- `final-tests`：851 项集成回归原始日志与结果；`native-ui-source.json` 为本机原生界面依据的指纹及行号。

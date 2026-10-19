# 13: 占城决定绑定原待决城市

**What to build:** 保留、拒绝、夷平和解放只处理用户请求的原城市与待决项。

**Blocked by:** 01 地块购买与可复用的未决动作契约

**Status:** resolved

- [x] 传入城市 ID 必须匹配原待决城市，提交前拒绝错误对象。
- [x] 结果确认后不在同一次重入中处理下一座城。
- [x] 销毁、归属变化、延迟和丢回应均有可观察行为测试。

## Context

[父规格](../spec.md)；基线 d3450a3。测试采用父规格已声明的公开 seam，不纳入人工并发问题。


## Answer

实现提交：`2349c5e`；合入集成 `4f195b2` 的合并提交：`6337295`。

- MCP `city_action(city_id, action)` 现在传递并记录城市 ID；直接 GameState 方法为 `resolve_city_capture(city_id, action)`。旧的只传 action 并选择下一座城的 Lua builder 已删除。城市射击沿用原所有者和路径。
- 在 captured／rebelled 待决源中匹配请求的完整城市 ID，并与原玩家城市集合、原坐标／地块交叉核对；保存该 source。keep 支持两种 source，reject 只支持 rebelled，raze／liberation 只支持 captured。错误城市、陈旧 ID、无待决项、资格 false／未知或缺能力均为零提交。
- 原生路径仍为 `CityManager.CanStartCommand(city, DESTROY, flags)` 严格 true 后单次 RequestCommand。ActionLifecycle 保留原城市、决定和作用域；进入原生命令前保存数字回执，命令后不读取已销毁／转移的旧 userdata。MCP 与直接 GameState 的相同意图重入只确认原请求，本次调用不会继续处理下一座城。
- keep 要求 exact CityCommandStarted、原待决项消失、原城市仍在原地块且归原玩家。raze 还必须有原 owner／完整 cityID 的 CityRemovedFromMap、原地块无城市且原城市集合不存在该 ID；nil 单独不能证明夷平。
- liberation 提交前绑定 founder 或 owner-before-occupation；reject 提交前绑定原生 `PlayerManager.GetFreeCitiesPlayerID()`。确认要求对应新 owner／完整 cityID 的 CityTransfered 回执、原地块上的新城市归属与 founder 一致、原 owner 集合中的原城市消失。后续其它转移事件不会覆盖原事件证据。

原版依据：`Base/Assets/UI/Popups/RazeCity.lua:15–50,83–90` 与 `Expansion2/UI/Additions/DisloyalCityChooser.lua:1–38` 的决定、flags 和原所属玩家字段；`Base/Assets/UI/ActionPanel.lua:844` 的 CityCommandStarted；`WorldView/CityBannerManager.lua:1929` 的 CityRemovedFromMap；`Expansion2/UI/Loaders/TutorialLoader_Expansion1.lua:282–286` 将 CityTransfered 参数保存为新所属玩家和城市 ID；`BlackDeathScenario/Scripts/BlackDeathScenario.lua:46` 读取原生自由城市玩家 ID。没有推断 CityCommandStarted 的 iData1 是 directive，也没有硬编码自由城市玩家号。

验证：公开 MCP／GameState、真实生命周期和实际 Lua 5.1，仅连接与原生引擎为 adapter。首先复现旧实现将请求 131073 错误应用到 65537；随后按错误身份、keep、raze、归属转移、输入／能力缺失、事件保留逐项红绿实施。领域 **70 项通过**。合并最新集成后，城市决定／射击、地块购买、MCP／GameState、未决调度／scope 和新合入单位效果的相关回归 **699 项通过（7.32s）**。8 个修改的 Python 文件 AST 解析及 `git diff --check` 通过。

覆盖正常完成、延迟、丢回应、重复同意图、captured／rebelled 下一项隔离、错误目标／归属／传承／事件、部分效果、原生抛错、取消、读回错误、世界切换锁定、提交前 scope 变化和跨城市／单位／帝国／回合门禁。未运行实机，没有启动或修改真实对局，没有 push。

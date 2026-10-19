# B06-01：补齐原生迷雾移动参数

Status: resolved
Blocked by: none
Spec: ../spec.md

职责：在现有move构建器补齐原生ignore-fog参数，保留和平边界、完整确认门禁及路径验证。现有公共测试缝隙内TDD，处理缺失标志，不新增重试。工作者拥有相关移动模块、测试及本ticket；主代理负责实机、progress。


## Implementation

本机 Civ6Common.lua RequestMoveOperation 使用 MOVE_IGNORE_UNEXPLORED_DESTINATION 以允许不可见目标。普通移动参数加入该原生标志（本机 16384），不引入原生 UI 的无条件 ATTACK；仅目标存在经实时外交确认的交战单位时合并 ATTACK（本机 16，合并 16400）。相同最终参数先经目标 CanStartOperation 核对，再沿既有路径与 segment 见证流程提交一次。

缺失／非数值／非正整数／非有限 fog 标志、缺 PARAM_MODIFIERS，以及敌对目标所必需但无效的 ATTACK 标志，均在安装见证及提交前显式 MOVE_MODIFIER_UNAVAILABLE。和平目标不依赖 ATTACK 枚举。路径、未来首步拒绝、完整身份／世界／回合／token、位移及 segment 确认未修改。

TDD：不可见可达目标要求原生 fog 标志的公共 GameState／Lua 用例先红后绿，同一 checked/submitted params、和平／中立／capture 的明确标志值及一次提交验证通过；缺失或格式错误 fog／所需 ATTACK 标志均先红后绿。test_move_target_lua、test_action_lua_execution、test_action_confirmation、test_action_scope 共 235 passed；compileall 与 diff --check 通过。离线验证确认参数契约，是否解决本轮 T31 原生无声拒绝仍待主代理从已确认检查点实测迷雾目标、推进 T32 并独立审查。

## Live verification

实施1e573fb，集成440bc44；235相关离线通过、双审查无实质发现。#483揭示原目标31,22被中立非斯勇士占据（#482可见后正确NO_PATH零提交）；#503确认新目标29,22仍不可见且已探索；#504修复后普通move实际到达29,22，#508确认T32→T33，#509保存gold_20261019_t033_b06_verified。未加和平ATTACK、不放宽位置/segment见证。

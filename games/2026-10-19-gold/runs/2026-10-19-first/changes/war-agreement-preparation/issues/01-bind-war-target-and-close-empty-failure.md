# B08-01：完整战争条款与失败空草稿收尾

Status: ready-for-agent
Blocked by: none
Spec: ../spec.md

职责：在既有trade边界修复target/WarType/原生option的传播与校验，保留模型决策；修复有原始no-send receipt的原生清空草稿安全退出，完整保留ownership与no-replay。现有公共缝隙TDD。工作者拥有相关server/trade实现、测试和本ticket；父代理负责实机、防御、恢复、progress。

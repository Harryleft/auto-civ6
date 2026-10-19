# B10-01：原生晋升命令与单次确认

Status: ready-for-agent
Blocked by: none
Spec: ../spec.md

替换promote_unit直接状态修改，绑定当前原生CanStartCommand晋升选项并提交PROMOTE，保留ActionLifecycle所有权及不重发。现有公共缝隙TDD。工作者拥有unit_transforms及必要GameState/tests/架构说明和本ticket，父拥有progress与实机。不得操作游戏。

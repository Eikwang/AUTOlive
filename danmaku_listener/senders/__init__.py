"""AutoDanmu 发送子包（ADR-002 / /autoplan 批准计划 T4-T6/T8/T11）

- pipeline: 单一命令管线（R32——ws/REST 双入口收敛）
- guard:    风控六件套（开关/限速/过滤/dry-run/审计/熔断）
- registry: 平台 sender 注册表 + 监听房间只读判定（F2/E5）
- bilibili: HTTP API sender（复用引擎 cookie 解析器，E4）
- audit:    审计日志（权威对账，fail-closed R25 + 幂等索引持久化 F9）

发送语义速查（DX-F1/R36）：守卫命中=拒绝不排队；unknown 不计熔断；dry_run 非成功语义。
"""

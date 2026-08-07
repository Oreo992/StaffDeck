# StaffDeck v0.3 Migration

## 目标

将上游 `v0.3.0` 的 Harness v2、安全沙箱、能力清单、文件交付和执行日志迁入当前分支，同时保留 QQQ · Claude 的自主循环与 SOP 外环监督。最终保留两个执行器，但共用一套 Harness 基础设施：

```text
Chat API → Runtime Router
  ├─ legacy session → Harness v2 Agent
  └─ claude_supervised session → Claude Agent SDK
          ↓ shared
  Capability / Sandbox / Artifact / Turn Store / SOP Supervisor
```

持久化的 `runtime_mode` 仍为 `legacy | claude_supervised`。`legacy` 是历史会话兼容标识，内部实现可由 Harness v2 完全接管；已有会话不改模式，Claude 失败也不自动回退。

## 当前基线

- 上游基线：`v0.3.0`（`f9cc988`）。
- 当前分支与上游相差约 417 个文件；不能直接覆盖或整分支合并。
- 当前 Claude Runtime 已具备连续 SDK session、SOP Supervisor、证据审计、修复循环和显式文件发布。
- 第一批已迁入纯基础合同：`app.harness.contracts`、`errors`、`execution_context`。
- `app.runtime.v2_compat` 已保证工具副作用双向映射：`read → read`、`write → write`、`destructive → delete`。
- M1-A 已迁入五类生命周期表、Turn receipt Store 与 Session lease Store；目前没有生产调用者。
- Harness v2 尚未进入生产路由。

## 不可破坏的行为契约

1. Claude 可以在一个 Segment 内自主循环；StaffDeck 只在段外审计 SOP。
2. 模型不能直接完成 Graph 节点或绕过审批、权限与幂等门禁。
3. `send_file` 只交付下载文件；`publish_file` 必须由模型主动调用。
4. 写入、删除和公网发布不得因恢复、断流或 Runtime 切换重复执行。
5. 历史会话继续使用创建时的 `runtime_mode`，不做隐式迁移。

## 分阶段迁移

### M0：合同冻结（进行中）

- 引入不依赖数据库的 Harness 合同、错误和执行上下文。
- 建立当前 `HarnessTool` 与 v0.3 `HarnessToolSpec` 的无损转换。
- 用回归测试冻结 Claude session、SOP 审计、附件和副作用语义。

### M1：持久化生命周期

- [x] 增量加入 `HarnessTaskFrameRecord`、`HarnessRunRecord`、`HarnessTurnRecord`、`HarnessSessionLeaseRecord`、`HarnessInvocationRecord`。
- [x] 迁入 session lease 和 turn receipt，验证并发 fence、完成响应重放与请求摘要冲突。
- [ ] 迁入 TaskFrame store 和 invocation replay policy。
- [ ] 使用独立数据库 Session 做影子写入；不能直接复用 AgentLoop 的长事务 Session。
- 先影子写入并核对，不改变现有响应路径；数据库升级必须可重复执行。

### M2：统一能力层

- 用 Capability Manifest 投影现有 Tool、Knowledge、General Skill 和文件能力。
- Harness Capability Invoker 继续调用现有 `ToolExecutor`、权限、确认和审计实现。
- 未声明副作用继续按保守规则分类，转换过程禁止降级。

### M3：Legacy 由 Harness v2 接管

- 以租户开关启用，默认关闭。
- 新建 `legacy` 会话先进入 Harness v2；历史会话按兼容策略逐步放开。
- 出错明确终止，不在同一 turn 内回退旧 AgentLoop，避免重复副作用。

### M4：Claude 共用 v2 基础设施

- Claude SDK 保持自主 loop，只替换工具执行、工作区、附件、receipt 和 lease 实现。
- SOP Supervisor 仍是外环控制器，Graph 状态只在证据审计通过后提交。
- 完成同一 session 的恢复、取消、审批和重复调用验证。

### M5：切换与清理

- 对比任务完成率、模型调用数、修复轮次、越权拦截和执行耗时。
- 全量通过后移除旧 StepAgent continuation、Reflection 和重复文件链路。
- 最后清理旧品牌名称；不在迁移中途同时做大范围 UI 改名。

## 每阶段准入门槛

- 新增测试先失败、实现后通过。
- Legacy 与 Claude 既有回归不得新增失败。
- 数据库升级在已有 SQLite 数据副本上执行两次结果一致。
- 写入工具重放测试必须证明底层副作用只发生一次。
- 预览环境真实验证普通对话、SOP、文件下载、公网发布、取消与恢复后，才可打开下一阶段开关。

# 操作与输入输出

## 角色与业务闭环

| 角色 | 可执行操作 |
|---|---|
| 管理员 admin | 创建/重置/停用用户，设置角色，查看与分配任务；不代替复核员进行复核 |
| 研判员 analyst | 提交原始材料，查看、重试、导出自己的任务 |
| 复核员 reviewer | 查看明确分配的任务，核对语境与证据，追加复核意见；不得复核自己提交的材料 |
| 审计员 auditor | 只读查看与导出分配任务及相关操作记录 |

登录后的临时口令必须先修改。重置密码、变更角色和停用会撤销旧会话；取消分配会撤销任务访问。浏览器多标签页切换账号会清除旧材料，迟到响应不应进入新会话。

研判员提交 → 材料入库和分窗 → 工作流接收任务 → 模型执行 → 服务端校验 → 管理员分配 → 复核员核对 → 报告导出。所有任务仍要求人工复核。

## 输入格式

支持 UTF-8（可含 BOM）的 TXT / JSON / JSONL / CSV。业务入口最多 1 MiB 与 100 个窗口，超限拒绝；不默默截断原文。TXT 作为一个消息，未知作者、回复和时间保持空值。

JSON/JSONL 推荐使用显式事件与消息列表；消息必须有真实来源中的稳定 ID，不能把相邻独立评论拼成同一事件：

```json
{
  "case_id": "synthetic-demo-001",
  "task": "language_observation_decision",
  "messages": [
    {"message_id": "m1", "author_key": "demo-a", "reply_to": null,
     "timestamp": null, "text": "这次处理效率太低，我要求说明。"},
    {"message_id": "m2", "author_key": "demo-b", "reply_to": "m1",
     "timestamp": null, "text": "可以质疑处理流程，请不要攻击个人。"}
  ]
}
```

CSV 表头至少包含 `text` 或 `content`。建议提供 `message_id, author_key, reply_to, timestamp, thread_id`。flat JSONL/CSV 只有显式 `thread_id / conversation_id / event_id` 才合并为对话；只有 `case_id` 不证明消息属于同一事件。事件顺序和来源行引用保留，缺少关系保持未知。

`author_key` 是来源内的作者标识，不是实名或跨平台身份核验结果。相同昵称不能自动归并为同一人。脱敏映射在单位受控系统管理。

## 研判结果

| decision | 含义 |
|---|---|
| supported | 有攻击行为的语言观察，且有逐字引用支持 |
| not_supported | 当前发言未支持攻击观察，可仍含转述/求助/正常批评标签 |
| insufficient_context | 必要语境不足，列明缺少信息 |

结果包含 `observations, evidence, target_actor_key, rationale, context_flags, missing_info`。攻击标签包括直接辱骂、隐含贬损、变形辱骂、威胁、动员攻击、多次攻击和多人攻击等；语境标签包括正常批评、消费投诉、转述、教学分析、求助与身份未知。完整协议以 `backend/service/schema.py` 为准。

模型 evidence 使用 `message_id + quote + occurrence`。服务端在原始 Unicode 文本中精确定位引用，转换为 `start/end`；无法匹配、错误模型 ID、预算异常、截断或矛盾输出进入明确异常/复核状态。结构正确与引用真实只是必要条件，不证明语义判断正确。

多人发言攻击同一对象不能自动推断为组织协同。个人信息披露模式不能证明信息真实、披露未经同意或满足违法构成。

## 状态、复核与导出

任务可能处于 `preparing / dispatching / running / waiting_model / completed / completed_with_errors / dispatch_failed / import_failed`。错误与缺上下文不转换成“未发现攻击”。关闭调度时任务可以入库排队；`completed` 表示机器处理结束，仍需要复核。

复核意见为 `confirm_observation / request_context / reject_observation`，附理由与当前版本。复核追加保存，不覆盖模型原结果；分配变更后的旧意见留存，但不冒充当前指定复核人的意见。

导出 JSON 保存材料与窗口引用、哈希、任务、pipeline、校验后结果、人员与复核记录。`human_gold` 与 `legal_determination` 当前为 null；普通业务复核不会自动生成训练金标。训练标签必须另经双人标注与仲裁。

## 接口与限制

客户机使用前端 `/api/...`，密钥保留于前端服务器。内部后端 `/workbench/*` 要求 Bearer + 独立用户会话；写请求还按当前页面上下文校验。第三方调用需实现完整会话与上下文协议，不把服务端 Bearer 当成用户登录。

任务与审计默认每页 30 条，最多 100；cursor 绑定用户与权限，每页重新校验分配。审计在当前 API 中追加留存，服务器管理员仍能直接修改 SQLite，尚未实现外部防篡改存证。

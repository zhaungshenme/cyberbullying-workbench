export type Data = Record<string, unknown>;
export const asData = (value: unknown): Data => value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {};
export const asArray = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const show = (value: unknown): string => value === null || value === undefined ? '未提供' : typeof value === 'string' ? value : JSON.stringify(value);
export function formatTime(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供';
  const date = typeof value === 'number' && Number.isFinite(value) ? new Date(value < 1e12 ? value * 1000 : value)
    : typeof value === 'string' ? new Date(value) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : show(value);
}

export const STATUS: Record<string, string> = {
  pending: '等待处理', queued: '已排队', submitting: '正在提交工作流', processing: '处理中', running: '处理中',
  waiting_model: '等待模型服务', analyzing: '正在分析', submitted: '已接收', accepted: '已接收',
  completed: '处理完成', succeeded: '处理完成', completed_with_errors: '完成，需复核', failed: '处理失败', needs_review: '需要人工复核',
  invalid_output: '模型输出格式无效', inconsistent_output: '模型结果存在矛盾', truncated_output: '模型输出被截断',
  context_required: '需要补充上下文', insufficient_context: '上下文不足',
  dispatching: '正在提交工作流', dispatch_failed: '工作流提交失败', import_failed: '原始数据导入失败', acked: '已接收',
};
export const DECISION: Record<string, string> = {
  supported: '观察得到文本证据支持',
  not_supported: '当前文本未支持攻击观察',
  insufficient_context: '上下文不足',
};
export const OBSERVATION: Record<string, string> = {
  direct_personal_insult: '直接人身辱骂', implicit_targeted_derogation: '隐含针对性贬低', obfuscated_personal_insult: '变形人身辱骂',
  threat_expression: '威胁表达', hostile_death_wish: '恶意死亡愿望', personal_information_exposure_pattern: '个人信息暴露表达',
  incitement_to_target: '煽动针对他人', sexualized_insult: '性化侮辱', repeated_targeted_attack: '重复针对性攻击',
  multi_actor_targeted_attack: '多人针对性攻击', explicit_coordination_expression: '明确协同攻击表达',
  quoted_insult: '引用辱骂', quoted_threat_without_endorsement: '引用威胁但未表示赞同', condemnation_of_attack: '谴责攻击',
  normal_criticism: '正常批评', consumer_complaint: '消费者投诉', educational_analysis: '教育或研究分析',
  fictional_dialogue: '虚构对话', help_or_report: '求助或举报', identity_unverified: '身份未核实', target_unresolved: '对象未确定',
  source_context_incomplete: '来源上下文不完整', consent_unknown: '同意状态未知', reported_attack_unverified: '被报告的攻击尚未核实',
  defensive_role_with_attack: '防御回应中含攻击', multiple_targets: '涉及多个对象',
};
export const MISSING_INFO: Record<string, string> = {
  author_key: '未知作者标识', author_identity: '发言者身份', target_actor_key: '被针对对象标识',
  relative_time_seconds: '相对时间', timestamp: '绝对时间', reply_to: '回复关系',
  source_context: '来源上下文', source_context_incomplete: '原始语境不完整', consent: '同意信息',
};

export function statusText(value: unknown): string {
  const text = show(value);
  return STATUS[text] || text;
}
export const TERMINAL = new Set(['completed', 'succeeded', 'completed_with_errors', 'failed', 'dispatch_failed', 'import_failed', 'needs_review', 'invalid_output', 'inconsistent_output', 'truncated_output', 'context_required']);

export function sourceMessage(windows: unknown, windowId: unknown, messageId: unknown): Data | null {
  const all = asArray(windows).map(asData);
  const preferred = all.find(window => window.id === windowId || window.window_id === windowId);
  const ordered = preferred ? [preferred, ...all.filter(window => window !== preferred)] : all;
  for (const window of ordered) {
    const message = asArray(window.messages).map(asData).find(item => item.message_id === messageId);
    if (message) return message;
  }
  return null;
}

export function evidenceMatches(evidence: Data, message: Data | null): boolean | null {
  if (!message || typeof message.text !== 'string' || typeof evidence.quote !== 'string'
    || typeof evidence.start !== 'number' || typeof evidence.end !== 'number') return null;
  if (!Number.isInteger(evidence.start) || !Number.isInteger(evidence.end) || evidence.start < 0 || evidence.end <= evidence.start) return false;
  // Python evidence offsets count Unicode code points, not JavaScript UTF-16 code units.
  return Array.from(message.text).slice(evidence.start, evidence.end).join('') === evidence.quote;
}

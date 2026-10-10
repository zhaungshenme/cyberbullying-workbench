const messages: Record<string, string> = {
  invalid_credentials: '账户名或密码不正确。', authentication_required: '请先登录工作台。',
  session_context_required: '当前页面尚未确认会话，请先同步当前会话。',
  session_context_changed: '会话已在其他页面或操作中改变，已清除旧材料。请明确同步当前会话后再操作。',
  session_invalid_or_expired: '登录已失效，请重新登录。', password_change_required: '请先更改临时密码。',
  role_forbidden: '当前账户没有执行此操作的权限。', forbidden: '服务认证失败，请联系管理员。',
  last_active_admin_protected: '必须保留至少一个启用的管理员账户。',
  password_must_have_12_to_256_characters: '密码须为 12 至 256 个字符。',
  current_password_invalid: '当前密码不正确。', new_password_must_differ: '新密码必须与当前密码不同。',
  user_not_found: '账户不存在或已不可访问。', invalid_user_fields: '账户信息不符合要求，请检查账户名、姓名与角色。',
  null_user_fields_not_allowed: '账户修改项不能为空。', username_exists: '账户名已存在，请使用其他账户名。',
  legacy_ownerless_assignment_forbidden: '此历史任务没有独立账户归属，仅管理员只读查看。请重新提交需协作复核的材料。',
  assignee_role_or_active_invalid: '分配对象须为已启用且角色相符的账户。', owner_cannot_review: '材料提交人不能复核自己的任务。',
  assigned_reviewer_required: '只有当前指定的复核员可以提交复核意见。',
  review_idempotency_key_conflict: '重复提交编号对应的复核内容已改变，请刷新任务后重试。',
  cannot_confirm_incomplete_model_coverage: '当前还有窗口未返回有效模型结果，不能确认观察。请补充上下文或否定观察，并记录原因。',
  task_not_terminal: '任务仍在处理，请等待处理完成后提交复核。',
  case_version_conflict: '材料版本已更新，请刷新任务并核对最新原文后再次复核。',
  pipeline_version_conflict: '任务使用的分析流程版本已改变，请联系管理员核查。', review_comment_required: '请填写复核说明。',
};
export function requestErrorMessage(body: Record<string, unknown>, status: number): string {
  const detail = typeof body.error === 'string' ? body.error : typeof body.detail === 'string' ? body.detail : '';
  if (messages[detail]) return messages[detail];
  if (/[\u3400-\u9fff]/.test(detail)) return detail;
  if (status === 401) return '登录已失效，请重新登录。';
  if (status === 403) return '当前账户没有执行此操作的权限。';
  if (status === 404) return '记录不存在，或当前账户无权访问。';
  if (status === 409) return '记录状态已改变，请刷新后再操作。';
  if (status === 413) return '请求超过大小上限，不会自动截断。';
  if (status === 429) return '请求过于频繁，请稍后重试。';
  if (status >= 500) return '服务暂时无法完成请求，请稍后重试或联系管理员。';
  return '提交的信息不符合要求，请检查内容后重试。';
}

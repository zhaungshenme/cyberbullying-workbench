'use client';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ROLES, ROLE_NAME, REVIEW_NAME, canManage, canReview, type User, type Role } from '@/lib/access';
import { asArray, asData, show, formatTime, type Data } from '@/lib/display';
import { jsonRequest, StaleResponse, type RequestData } from '@/lib/client-api';

export function AccountPassword({ user, request, onChanged, onCancel, forced = false }: { user: User; request: RequestData; onChanged: (data: Data) => void; onCancel?: () => void; forced?: boolean }) {
  const [current, setCurrent] = useState(''), [password, setPassword] = useState(''), [confirm, setConfirm] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function change(event: FormEvent) {
    event.preventDefault(); setError('');
    if (password.length < 12 || password !== confirm || password === current) { setError('新密码至少 12 字符，必须与旧密码不同，两次输入须一致。'); return; }
    setBusy(true);
    try { const data = await request('/api/session/password', jsonRequest('POST', { current_password: current, new_password: password })); setCurrent(''); setPassword(''); setConfirm(''); onChanged(data); }
    catch (error) { if (!(error instanceof StaleResponse)) setError(error instanceof Error ? error.message : '修改失败。'); }
    finally { setBusy(false); }
  }
  return <form className={forced ? 'login-card' : 'panel account-password'} onSubmit={change}>
    <h2>{forced ? '首次登录，请更改临时密码' : '更改我的密码'}</h2><p className="muted">{user.display_name} · {ROLE_NAME[user.role]}。更改密码后，其他已登录会话会退出。</p>
    <label htmlFor="current-password">当前密码</label><input id="current-password" type="password" autoComplete="current-password" value={current} onChange={event => setCurrent(event.target.value)} maxLength={256} required />
    <label htmlFor="new-password">新密码（至少 12 字符）</label><input id="new-password" type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} minLength={12} maxLength={256} required />
    <label htmlFor="confirm-password">再次输入新密码</label><input id="confirm-password" type="password" autoComplete="new-password" value={confirm} onChange={event => setConfirm(event.target.value)} minLength={12} maxLength={256} required />
    {error && <p className="error-box" role="alert">{error}</p>}<div className="button-row"><button className="primary" disabled={busy}>{busy ? '正在更改…' : '更改密码'}</button>{onCancel && <button type="button" className="ghost" onClick={onCancel}>{forced ? '退出登录' : '取消'}</button>}</div>
  </form>;
}

function UserEditor({ account, request, onSaved }: { account: Data; request: RequestData; onSaved: () => void }) {
  const [role, setRole] = useState(show(account.role) as Role), [name, setName] = useState(show(account.display_name));
  const [active, setActive] = useState(account.active === true), [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const body: Data = { role, display_name: name, active }; if (password) body.password = password;
      await request(`/api/workbench/users/${encodeURIComponent(show(account.id))}`, jsonRequest('PATCH', body)); setPassword(''); onSaved();
    } catch (error) { if (!(error instanceof StaleResponse)) setError(error instanceof Error ? error.message : '保存失败。'); }
    finally { setBusy(false); }
  }
  return <details className="account-row"><summary>{show(account.display_name)} <span className="muted">@{show(account.username)} · {ROLE_NAME[show(account.role) as Role] || show(account.role)} · {account.active ? '启用' : '停用'}</span></summary>
    <form onSubmit={save} className="account-editor"><label>显示姓名<input value={name} onChange={event => setName(event.target.value)} maxLength={80} required /></label><label>角色<select value={role} onChange={event => setRole(event.target.value as Role)}>{ROLES.map(role => <option key={role} value={role}>{ROLE_NAME[role]}</option>)}</select></label><label className="checkbox-label"><input type="checkbox" checked={active} onChange={event => setActive(event.target.checked)} />启用账户</label><label>重置为临时密码（留空则不重置）<input type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} minLength={12} maxLength={256} /></label><p className="fineprint">修改角色、停用或重置密码会撤销该用户已登录会话。新建、重置密码后用户须自行更改。</p>{error && <p className="error-box">{error}</p>}<button className="secondary" disabled={busy}>{busy ? '保存中…' : '保存账户'}</button></form>
  </details>;
}

export function UsersPanel({ request, accounts, onRefresh }: { request: RequestData; accounts: Data[]; onRefresh: () => void }) {
  const [username, setUsername] = useState(''), [name, setName] = useState(''), [role, setRole] = useState<Role>('analyst'), [password, setPassword] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [notice, setNotice] = useState('');
  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(''); setNotice('');
    try { await request('/api/workbench/users', jsonRequest('POST', { username, display_name: name, role, password })); setUsername(''); setName(''); setPassword(''); setNotice('账户已创建，首次登录须更改临时密码。'); onRefresh(); }
    catch (error) { if (!(error instanceof StaleResponse)) setError(error instanceof Error ? error.message : '创建失败。'); }
    finally { setBusy(false); }
  }
  return <section className="panel admin-panel"><div className="card-title"><h2>账户与角色管理</h2><button className="ghost" onClick={onRefresh}>刷新账户</button></div><p className="muted">研判员提交材料；复核员复核分配任务；审计员只读查看分配任务与审计记录。</p>
    <form onSubmit={create} className="create-user"><label>账户名<input value={username} onChange={event => setUsername(event.target.value)} pattern="[A-Za-z0-9_.-]{3,64}" minLength={3} maxLength={64} autoComplete="off" required /></label><label>显示姓名<input value={name} onChange={event => setName(event.target.value)} maxLength={80} required /></label><label>角色<select value={role} onChange={event => setRole(event.target.value as Role)}>{ROLES.map(role => <option key={role} value={role}>{ROLE_NAME[role]}</option>)}</select></label><label>临时密码<input type="password" autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} minLength={12} maxLength={256} required /></label><button className="primary" disabled={busy}>{busy ? '创建中…' : '创建账户'}</button></form>
    {error && <p className="error-box" role="alert">{error}</p>}{notice && <p className="success-note">{notice}</p>}<div>{accounts.map(account => <UserEditor key={`${show(account.id)}-${show(account.role)}-${show(account.active)}-${show(account.display_name)}`} account={account} request={request} onSaved={onRefresh} />)}</div>
  </section>;
}

export function TaskRoles({ task, taskId, user, accounts, request, onRefresh }: { task: Data; taskId: string; user: User; accounts: Data[]; request: RequestData; onRefresh: () => void }) {
  const reviewer = asData(task.reviewer), auditor = asData(task.auditor);
  const [reviewerId, setReviewerId] = useState(''), [auditorId, setAuditorId] = useState('');
  const [outcome, setOutcome] = useState('request_context'), [comment, setComment] = useState('');
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const reviewKey = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => { setReviewerId(typeof reviewer.id === 'string' ? reviewer.id : ''); setAuditorId(typeof auditor.id === 'string' ? auditor.id : ''); }, [reviewer.id, auditor.id]);
  const reviewAllowed = canReview(user, reviewer.id);
  const caseVersion = task.case_version ?? asData(task.case).current_version;
  const legacy = typeof asData(task.owner).id !== 'string';
  const completeCoverage = asData(task.coverage).all_windows_model_completed === true;
  async function assign(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try { await request(`/api/workbench/tasks/${encodeURIComponent(taskId)}/assign`, jsonRequest('POST', { reviewer_id: reviewerId || null, auditor_id: auditorId || null })); onRefresh(); }
    catch (error) { if (!(error instanceof StaleResponse)) setError(error instanceof Error ? error.message : '分配失败。'); }
    finally { setBusy(false); }
  }
  async function review(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const fingerprint = JSON.stringify([taskId, caseVersion, outcome, comment]);
      if (reviewKey.current?.fingerprint !== fingerprint) {
        const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes);
        reviewKey.current = { fingerprint, key: Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('') };
      }
      await request(`/api/workbench/tasks/${encodeURIComponent(taskId)}/review`, jsonRequest('POST', { outcome, comment, case_version: caseVersion, idempotency_key: reviewKey.current.key })); setComment(''); onRefresh();
    }
    catch (error) { if (!(error instanceof StaleResponse)) setError(error instanceof Error ? error.message : '复核提交失败。'); }
    finally { setBusy(false); }
  }
  return <section className="panel human-review"><h3>任务分工与人工复核</h3><div className="meta"><span>提交人：{show(asData(task.owner).display_name)}</span><span>复核员：{show(reviewer.display_name)}</span><span>审计员：{show(auditor.display_name)}</span><span>复核状态：{{ unassigned: '尚未分配', pending: '等待复核', reviewed: '已有复核' }[show(task.review_status)] || show(task.review_status)}</span><span>材料版本：{show(caseVersion)}</span></div>
    {legacy && <p className="inline-warning">此为升级前的历史任务，尚无独立账户归属，仅管理员只读查看。请按当前账户重新提交需协作复核的材料。</p>}{canManage(user) && !legacy && <form className="assignment-form" onSubmit={assign}><label>分配复核员<select value={reviewerId} onChange={event => setReviewerId(event.target.value)}><option value="">未分配</option>{accounts.filter(account => account.active === true && account.role === 'reviewer').map(account => <option key={show(account.id)} value={show(account.id)}>{show(account.display_name)} · {show(account.username)}</option>)}</select></label><label>分配审计员<select value={auditorId} onChange={event => setAuditorId(event.target.value)}><option value="">未分配</option>{accounts.filter(account => account.active === true && account.role === 'auditor').map(account => <option key={show(account.id)} value={show(account.id)}>{show(account.display_name)} · {show(account.username)}</option>)}</select></label><button className="secondary" disabled={busy}>保存分配</button></form>}
    {reviewAllowed && <form className="review-form" onSubmit={review}><h4>追加人工复核意见</h4><p className="muted">请核对所有窗口、证据和上下文。意见独立保存并关联当前材料版本，模型观察保留原样。</p><label>复核结论<select value={outcome} onChange={event => setOutcome(event.target.value)}>{['confirm_observation', 'request_context', 'reject_observation'].map(value => <option key={value} value={value} disabled={value === 'confirm_observation' && !completeCoverage}>{REVIEW_NAME[value]}</option>)}</select></label><label>复核说明<textarea value={comment} onChange={event => setComment(event.target.value)} maxLength={2000} rows={4} required /></label><button className="primary" disabled={busy || typeof caseVersion !== 'number' || !comment.trim() || (outcome === 'confirm_observation' && !completeCoverage)}>{busy ? '保存中…' : '保存复核意见'}</button></form>}
    {error && <p className="error-box" role="alert">{error}</p>}{asArray(task.reviews).map((value, index) => { const review = asData(value); return <article className="review-record" key={show(review.id) + index}><div className="card-title"><strong>{REVIEW_NAME[show(review.outcome)] || show(review.outcome)}</strong><span className="muted">{formatTime(review.created_at)}</span></div><p className="preserve">{show(review.comment)}</p><p className="meta">复核员：{show(asData(review.reviewer).display_name)} · 材料版本：{show(review.case_version)}</p><details><summary>关联的材料与模型版本</summary>{asArray(review.references).map((value, index) => { const reference = asData(value); return <p className="meta" key={index}>窗口 {show(reference.window_id)} · 分析任务 {show(reference.job_id)} · 流程 {show(reference.pipeline_version)} · 输入哈希 {show(reference.input_hash)}</p>; })}</details></article>; })}
  </section>;
}

export function AuditPanel({ records, onRefresh, onMore, nextCursor, busy }: { records: Data[]; onRefresh: () => void; onMore: () => void; nextCursor: string | null; busy: boolean }) {
  const names: Record<string, string> = { login_succeeded: '登录成功', login_failed: '登录失败', logout: '退出', password_changed: '更改密码', user_created: '创建账户', user_updated: '修改账户', user_password_reset: '重置临时密码', user_disabled: '停用账户', task_list_read: '查看任务列表', audit_read: '查看审计记录', task_submitted: '提交材料', task_assigned: '分配任务', task_reviewed: '保存复核', task_read: '查看任务', task_exported: '导出报告' };
  return <section className="panel audit-panel"><div className="card-title"><h2>操作审计</h2><button className="ghost" onClick={onRefresh}>刷新记录</button></div><p className="muted">记录操作者、时间、动作和任务编号。原文与复核说明请在获授权的任务详情查看。</p><div className="table-scroll"><table><thead><tr><th>序号</th><th>时间</th><th>操作者</th><th>动作</th><th>任务</th></tr></thead><tbody>{records.map((record, index) => <tr key={show(record.seq) + index}><td>{show(record.seq)}</td><td>{formatTime(record.created_at)}</td><td>{show(asData(record.actor).display_name)}</td><td>{names[show(record.action)] || show(record.action)}</td><td><code>{show(record.submission_id)}</code></td></tr>)}</tbody></table></div>{!records.length && <p className="muted">当前没有可见的审计记录。</p>}<div className="button-row">{nextCursor && <button className="secondary" disabled={busy} onClick={onMore}>{busy ? '正在加载…' : '加载更多记录'}</button>}<span className="muted">{busy ? '正在读取列表…' : `已加载 ${records.length} 条记录`}</span></div></section>;
}

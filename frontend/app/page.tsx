'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { EXAMPLES, inspectInput, MAX_INPUT_BYTES, type Preview } from '@/lib/input';
import { asArray, asData, DECISION, evidenceMatches, OBSERVATION, MISSING_INFO, show, formatTime, sourceMessage, statusText, TERMINAL, type Data } from '@/lib/display';

import { canAudit, canManage, canSubmit, ROLE_NAME, parseUser, type User } from '@/lib/access';
import { ApiError, StaleResponse, readApi, jsonRequest } from '@/lib/client-api';
import { AccountPassword, UsersPanel, TaskRoles, AuditPanel } from './account-panels';
import { isAuthMutation } from '@/lib/auth-queue';
import { requiresContext, sessionContext, type SessionSnapshot } from '@/lib/session-context';
import { subscribeSessionChanges } from '@/lib/session-sync';
import { CursorPager, pagePath } from '@/lib/cursor-pager';
function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
}

function Badge({ value }: { value: unknown }) {
  const status = typeof value === 'string' ? value : '';
  const style = ['failed', 'dispatch_failed', 'import_failed', 'invalid_output', 'inconsistent_output', 'truncated_output'].includes(status) ? 'bad'
    : ['succeeded', 'completed'].includes(status) ? 'good' : 'wait';
  return <span className={`badge ${style}`}>{statusText(value)}</span>;
}

function ValueList({ value, dictionary }: { value: unknown; dictionary?: Record<string, string> }) {
  if (!Array.isArray(value)) return <p className="muted">未提供</p>;
  if (!value.length) return <p className="muted">返回列表为空</p>;
  return <div className="tags">{value.map((item, index) => <span className="tag" key={index}>{dictionary?.[show(item)] || show(item)}</span>)}</div>;
}

function ObservationCard({ job, windows, index, model }: { job: Data; windows: unknown; index: number; model?: unknown }) {
  const result = asData(job.result);
  const hasResult = job.result !== null && job.result !== undefined && Object.keys(result).length > 0;
  return <article className="result-card">
    <div className="card-title"><div><span className="eyebrow">分析任务 {index + 1}</span><h3>{hasResult ? DECISION[show(result.decision)] || show(result.decision) : '暂未返回可展示的观察结果'}</h3></div><Badge value={job.status} /></div>
    <div className="meta"><span>窗口：{show(job.window_id)}</span><span>模型：{show(job.model_name ?? asData(model).name)}</span></div>
    {job.error_code !== null && job.error_code !== undefined && <p className="inline-warning">异常代码：{show(job.error_code)}</p>}
    {!hasResult ? <p className="muted">等待、失败或无效输出不代表“未发现攻击”。请查看任务状态与异常信息。</p> : <>
      <h4>观察类别</h4><ValueList value={result.observations} dictionary={OBSERVATION} />
      <h4>逐字证据</h4>
      {Array.isArray(result.evidence) && result.evidence.length > 0 ? <div className="evidence-list">{result.evidence.map((value, evidenceIndex) => {
        const evidence = asData(value);
        const message = sourceMessage(windows, job.window_id, evidence.message_id);
        const matches = evidenceMatches(evidence, message);
        return <div className="evidence" key={evidenceIndex}>
          <blockquote>{show(evidence.quote)}</blockquote>
          <div className="meta"><span>消息：{show(evidence.message_id)}</span><span>位置：{show(evidence.start)} → {show(evidence.end)}</span></div>
          <span className={`evidence-check ${matches === false ? 'alert-text' : ''}`}>{matches === true ? '引用与窗口原文逐字匹配' : matches === false ? '引用与窗口原文不匹配，请复核' : '原文或位置信息缺失，无法在页面核对'}</span>
        </div>;
      })}</div> : <p className="muted">未返回逐字证据</p>}
      <h4>观察理由</h4><p className="preserve">{show(result.rationale)}</p>
      <div className="result-columns"><div><h4>上下文提示</h4><ValueList value={result.context_flags} dictionary={{ ...OBSERVATION, identity_unverified: '发言者身份未核实', source_context_incomplete: '原始语境不完整' }} /></div><div><h4>缺失信息</h4><ValueList value={result.missing_info} dictionary={MISSING_INFO} /></div></div>
      <p className="meta">观察对象标识：{show(result.target_actor_key)}</p>
    </>}
  </article>;
}

export default function Workbench() {
  const [user, setUser] = useState<User | null>(null), [checking, setChecking] = useState(true);
  const [needsSync, setNeedsSync] = useState(false), [syncBusy, setSyncBusy] = useState(false);
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [loginBusy, setLoginBusy] = useState(false), [loginError, setLoginError] = useState('');
  const [health, setHealth] = useState<Data | null>(null), [healthError, setHealthError] = useState('');
  const [caseName, setCaseName] = useState('文本观察任务'), [filename, setFilename] = useState('input.txt'), [content, setContent] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null), [inputError, setInputError] = useState(''), [submitting, setSubmitting] = useState(false), [demo, setDemo] = useState(false);
  const [taskId, setTaskId] = useState(''), [lookupId, setLookupId] = useState(''), [task, setTask] = useState<Data | null>(null), [taskError, setTaskError] = useState(''), [lastChecked, setLastChecked] = useState('');
  const [refresh, setRefresh] = useState(0), [downloadBusy, setDownloadBusy] = useState(false);
  const [tasks, setTasks] = useState<Data[]>([]), [accounts, setAccounts] = useState<Data[]>([]), [audit, setAudit] = useState<Data[]>([]), [queueError, setQueueError] = useState('');
  const [view, setView] = useState<'workspace' | 'users' | 'audit'>('workspace'), [changePassword, setChangePassword] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null), requestKey = useRef<{ fingerprint: string; key: string } | null>(null);
  const sessionEpoch = useRef(0), pending = useRef(new Set<AbortController>());
  const tasksPager = useRef(new CursorPager<Data>(item => show(item.submission_id))), auditPager = useRef(new CursorPager<Data>(item => show(item.seq)));
  const [taskPage, setTaskPage] = useState<{ nextCursor: string | null; busy: boolean }>({ nextCursor: null, busy: false });
  const [auditPage, setAuditPage] = useState<{ nextCursor: string | null; busy: boolean }>({ nextCursor: null, busy: false });

  function resetView(clearContext = true) {
    sessionEpoch.current += 1;
    if (clearContext) sessionContext.clearVisible();
    pending.current.forEach(controller => controller.abort()); pending.current.clear();
    setHealth(null); setHealthError(''); setContent(''); setFilename('input.txt'); setCaseName('文本观察任务'); setPreview(null); setInputError(''); setDemo(false);
    setTaskId(''); setLookupId(''); setTask(null); setTaskError(''); setLastChecked(''); setTasks([]); setAccounts([]); setAudit([]); setQueueError('');
    setPassword(''); setSubmitting(false); setDownloadBusy(false); setView('workspace'); setChangePassword(false); requestKey.current = null;
    tasksPager.current.reset(); auditPager.current.reset(); setTaskPage({ nextCursor: null, busy: false }); setAuditPage({ nextCursor: null, busy: false });
    if (fileInput.current) fileInput.current.value = '';
    // No material or task identity persists across accounts, including the old gateway key.
    if (typeof window !== 'undefined') window.sessionStorage.removeItem('workbench-last-task');
  }

  function invalidateSession(message = '其他标签页的会话正在改变，旧材料已清除。请明确同步当前会话后再操作。') {
    sessionContext.invalidate(); resetView(false); setUser(null); setUsername(''); setNeedsSync(true); setLoginError(message); setChecking(false);
  }

  const request = useCallback(async (path: string, options: RequestInit = {}): Promise<Data> => {
    const epoch = sessionEpoch.current;
    // Auth responses may update cookies: let the queue complete them before the
    // queued logout, while still discarding their stale returned account data.
    const controller = isAuthMutation(path, options.method) ? null : new AbortController();
    if (controller) pending.current.add(controller);
    const contextSnapshot = requiresContext(path, options.method) && sessionContext.context ? sessionContext.capture() : undefined;
    try {
      const data = await readApi(path, { ...options, signal: controller ? options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal : undefined });
      if (epoch !== sessionEpoch.current) throw new StaleResponse();
      if (contextSnapshot && !isAuthMutation(path, options.method) && !sessionContext.isCurrent(contextSnapshot)) throw new StaleResponse();
      return data;
    } catch (error) {
      if (epoch !== sessionEpoch.current || controller?.signal.aborted || (error instanceof Error && error.name === 'AbortError')) throw new StaleResponse();
      if (error instanceof ApiError && ['session_context_required', 'session_context_changed'].includes(error.code || '')) {
        invalidateSession(error.message); throw new StaleResponse();
      }
      if (error instanceof ApiError && error.status === 401 && path !== '/api/session') {
        resetView(); setUser(null); setNeedsSync(true); setLoginError('登录已失效，请同步会话或重新登录。'); throw new StaleResponse();
      }
      throw error;
    } finally { if (controller) pending.current.delete(controller); }
  }, []);

  function acceptSession(data: Data) {
    const next = parseUser(data.user); if (!next || !next.active) throw new Error('账户信息无效。');
    sessionContext.adopt(data.session_context, next.id);
    resetView(false); setUser(next); setLoginError(''); setNeedsSync(false);
  }
  function errorText(error: unknown): string { return error instanceof Error ? error.message : '请求失败。'; }

  useEffect(() => subscribeSessionChanges(() => invalidateSession()), []);

  useEffect(() => {
    let stopped = false;
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const automaticPassword = fragment.get('login'), automaticUsername = fragment.get('username');
    if (automaticPassword !== null || automaticUsername !== null) window.history.replaceState(null, '', window.location.pathname + window.location.search);
    window.sessionStorage.removeItem('workbench-last-task');
    async function initialize() {
      try {
        const data = automaticPassword !== null && automaticUsername !== null
          ? await request('/api/session', jsonRequest('POST', { username: automaticUsername, password: automaticPassword }))
          : await request('/api/session');
        if (!stopped) acceptSession(data);
      } catch (error) { if (!stopped && !(error instanceof StaleResponse) && (!(error instanceof ApiError) || error.status !== 401)) setLoginError(errorText(error)); }
      finally { if (!stopped) setChecking(false); }
    }
    void initialize(); return () => { stopped = true; };
  }, [request]);

  useEffect(() => {
    if (!user || user.must_change_password) return;
    let stopped = false;
    async function reload() {
      try {
        const data = await request('/api/session'); const next = parseUser(data.user);
        if (!stopped && next && (data.session_context !== sessionContext.context || next.id !== user!.id || next.role !== user!.role || next.must_change_password !== user!.must_change_password)) invalidateSession('当前会话已改变，旧材料已清除。请点击同步当前会话确认身份。');
      } catch (error) { if (!stopped && error instanceof ApiError && error.status === 401) invalidateSession('登录已失效，请同步会话或重新登录。'); }
    }
    window.addEventListener('focus', reload);
    return () => { stopped = true; window.removeEventListener('focus', reload); };
  }, [user, request]);

  useEffect(() => {
    if (!user || user.must_change_password) return;
    let stopped = false;
    const effectEpoch = sessionEpoch.current;
    async function load() {
      try { const data = await request('/api/workbench/health'); if (!stopped) { setHealth(data); setHealthError(''); } }
      catch (error) { if (!stopped && !(error instanceof StaleResponse)) setHealthError(errorText(error)); }
      if (stopped || effectEpoch !== sessionEpoch.current) return;
      await loadList('tasks', true);
      if (stopped || effectEpoch !== sessionEpoch.current) return;
      if (canManage(user)) try { const data = await request('/api/workbench/users'); if (!stopped) setAccounts(asArray(data.users).map(asData)); }
      catch (error) { if (!stopped && !(error instanceof StaleResponse)) setQueueError(errorText(error)); }
      if (stopped || effectEpoch !== sessionEpoch.current) return;
      if (canAudit(user) && view === 'audit') await loadList('audit', true);
    }
    void load(); return () => { stopped = true; };
  }, [user, request, refresh, view]);

  async function loadList(kind: 'tasks' | 'audit', first = false) {
    const pager = kind === 'tasks' ? tasksPager.current : auditPager.current;
    const ticket = pager.begin(first); if (!ticket) return;
    const epoch = sessionEpoch.current;
    function render() {
      const state = pager.state;
      if (kind === 'tasks') { setTasks(state.records); setTaskPage({ nextCursor: state.nextCursor, busy: state.busy }); }
      else { setAudit(state.records); setAuditPage({ nextCursor: state.nextCursor, busy: state.busy }); }
    }
    render();
    try {
      const data = await request(pagePath(`/api/workbench/${kind}`, ticket.cursor));
      if (epoch !== sessionEpoch.current) return;
      if (pager.accept(ticket, asArray(data[kind]).map(asData), data.next_cursor)) { render(); setQueueError(''); }
    } catch (error) {
      if (epoch === sessionEpoch.current && pager.fail(ticket)) { render(); if (!(error instanceof StaleResponse)) setQueueError(errorText(error)); }
    }
  }

  function reload() {
    // Invalidate outstanding pages at click time, before the effect starts the
    // new first page, so an old page cannot append to the refreshed snapshot.
    tasksPager.current.reset(); auditPager.current.reset(); setTasks([]); setAudit([]);
    setTaskPage({ nextCursor: null, busy: false }); setAuditPage({ nextCursor: null, busy: false });
    setRefresh(value => value + 1);
  }

  useEffect(() => {
    if (!content.trim()) { setPreview(null); setInputError(''); return; }
    try { setPreview(inspectInput(filename, content)); setInputError(''); }
    catch (error) { setPreview(null); setInputError(errorText(error)); }
  }, [content, filename]);

  useEffect(() => {
    if (!user || user.must_change_password || !taskId) return;
    let stopped = false, timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const data = await request(`/api/workbench/tasks/${encodeURIComponent(taskId)}`, { signal: controller.signal });
        if (stopped) return;
        setTask(data); setTaskError(''); setLastChecked(new Date().toLocaleTimeString('zh-CN'));
        if (!TERMINAL.has(show(data.status))) timer = setTimeout(poll, data.status === 'waiting_model' ? 8000 : 2500);
      } catch (error) {
        if (stopped || error instanceof StaleResponse) return;
        setTask(null); setTaskError(errorText(error));
        if (!(error instanceof ApiError) || ![401, 403, 404].includes(error.status)) timer = setTimeout(poll, 8000);
      }
    }
    void poll(); return () => { stopped = true; clearTimeout(timer); controller.abort(); };
  }, [user, taskId, refresh, request]);

  async function login(event: FormEvent) {
    event.preventDefault(); sessionContext.invalidate(); resetView(false); setLoginBusy(true); setLoginError('');
    try { const data = await request('/api/session', jsonRequest('POST', { username, password })); acceptSession(data); }
    catch (error) { if (!(error instanceof StaleResponse)) setLoginError(errorText(error)); }
    finally { setLoginBusy(false); }
  }
  async function logout() {
    let captured: SessionSnapshot;
    try { captured = sessionContext.capture(); } catch { invalidateSession('请先同步当前会话，再选择退出。'); return; }
    // Clear all prior-user content immediately and cancel every outstanding browser request.
    resetView(); setUser(null); setLoginError(''); setChecking(true);
    try { await readApi('/api/session', { method: 'DELETE' }, captured); setNeedsSync(false); }
    catch (error) {
      if (error instanceof ApiError && ['session_context_required', 'session_context_changed'].includes(error.code || '')) invalidateSession('退出未执行：会话已改变。请同步当前会话确认身份后再退出。');
      else { setNeedsSync(true); setLoginError(`退出请求未完成。请同步当前会话后再退出。${errorText(error)}`); }
    }
    finally { setChecking(false); }
  }
  async function synchronizeSession() {
    sessionContext.invalidate(); resetView(false); setUser(null); setSyncBusy(true); setLoginError('');
    const epoch = sessionEpoch.current;
    try { const data = await request('/api/session'); if (epoch === sessionEpoch.current) acceptSession(data); }
    catch (error) { if (!(error instanceof StaleResponse)) setLoginError(errorText(error)); }
    finally { setSyncBusy(false); }
  }
  async function readFile(file: File | undefined) {
    if (!file || !canSubmit(user)) return; const epoch = sessionEpoch.current;
    try {
      if (file.size > MAX_INPUT_BYTES) throw new Error('文件超过 1 MiB，不会自动截断。');
      const value = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
      if (epoch !== sessionEpoch.current) return;
      const parsed = inspectInput(file.name, value); setFilename(file.name); setContent(value); setPreview(parsed); setInputError(''); setDemo(false);
    } catch (error) { if (epoch === sessionEpoch.current) setInputError(errorText(error)); }
    if (epoch === sessionEpoch.current && fileInput.current) fileInput.current.value = '';
  }
  function selectTask(id: string) {
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(id)) { setTaskError('请输入有效任务编号。'); return; }
    setTaskId(id); setLookupId(id); setTask(null); setTaskError(''); reload();
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!preview || inputError || !canSubmit(user)) return;
    const epoch = sessionEpoch.current; setSubmitting(true); setTaskError('');
    const fingerprint = JSON.stringify([caseName, filename, content]);
    if (requestKey.current?.fingerprint !== fingerprint) requestKey.current = { fingerprint, key: idempotencyKey() };
    try {
      const accepted = await request('/api/workbench/analyze', jsonRequest('POST', { case_name: caseName, filename, content, idempotency_key: requestKey.current.key }));
      if (typeof accepted.submission_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(accepted.submission_id)) throw new Error('后端没有返回有效任务编号，请核查提交状态。');
      selectTask(accepted.submission_id); setTask(accepted);
    } catch (error) { if (!(error instanceof StaleResponse)) setTaskError(errorText(error)); }
    finally { if (epoch === sessionEpoch.current) setSubmitting(false); }
  }
  async function downloadReport() {
    const epoch = sessionEpoch.current, downloadId = taskId; setDownloadBusy(true);
    try {
      const report = await request(`/api/workbench/tasks/${encodeURIComponent(downloadId)}/report`);
      if (epoch !== sessionEpoch.current) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = `analysis-${downloadId}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (!(error instanceof StaleResponse)) setTaskError(errorText(error)); }
    finally { if (epoch === sessionEpoch.current) setDownloadBusy(false); }
  }

  const jobs = asArray(task?.jobs).map(asData), windows = asArray(task?.windows).map(asData), difyRuns = asArray(task?.dify_runs).map(asData);
  const results = jobs.filter(job => job.result !== null && job.result !== undefined).length, model = task?.model ?? health?.model;
  if (checking) return <main className="login-page"><div className="login-card"><div className="brand-mark">溯</div><h1>网暴行为溯源追踪工作台</h1><p className="muted">正在连接服务端…</p></div></main>;
  if (!user) return <main className="login-page"><form className="login-card" onSubmit={login}><div className="brand-mark">溯</div><span className="eyebrow">多人协作 · 工作流 · 可复核观察</span><h1>网暴行为溯源追踪工作台</h1><p className="muted">使用管理员分配的独立账户登录。</p><label htmlFor="login-username">账户名</label><input id="login-username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} maxLength={64} required /><label htmlFor="login-password">密码</label><input id="login-password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} maxLength={256} required />{loginError && <p className="error-box" role="alert">{loginError}</p>}<button className="primary" disabled={loginBusy}>{loginBusy ? '正在登录…' : '进入工作台'}</button><div className="button-row">{needsSync && <button type="button" className="secondary" disabled={syncBusy || loginBusy} onClick={() => void synchronizeSession()}>{syncBusy ? '正在同步…' : '同步当前会话'}</button>}</div><p className="fineprint">研判结果辅助人工复核，不作为司法认定或金标。</p></form></main>;
  if (user.must_change_password) return <main className="login-page"><AccountPassword key={user.id} user={user} request={request} onChanged={acceptSession} onCancel={() => void logout()} forced /></main>;

  return <div className="workbench">
    <header className="topbar"><div className="brand"><div className="brand-mark small">溯</div><div><strong>网暴行为溯源追踪工作台</strong><span>原始数据 → 工作流 → 文本观察 → 人工复核</span></div></div><div className="top-actions"><div className="account-identity"><strong>{user.display_name}</strong><span>{ROLE_NAME[user.role]} · @{user.username}</span></div><span className={`connection ${healthError ? 'offline' : ''}`}>{healthError ? '后端连接异常' : health ? '服务端已连接' : '正在检查服务端'}</span><button className="ghost" onClick={() => setChangePassword(value => !value)}>更改密码</button><button className="ghost" onClick={() => void logout()}>退出登录</button></div></header>
    <nav className="workspace-nav"><button className={view === 'workspace' ? 'selected' : ''} onClick={() => setView('workspace')}>任务工作台</button>{canManage(user) && <button className={view === 'users' ? 'selected' : ''} onClick={() => setView('users')}>账户管理</button>}{canAudit(user) && <button className={view === 'audit' ? 'selected' : ''} onClick={() => setView('audit')}>操作审计</button>}</nav>
    {changePassword && <div className="wide-section"><AccountPassword key={`${user.id}-${sessionEpoch.current}`} user={user} request={request} onChanged={acceptSession} onCancel={() => setChangePassword(false)} /></div>}
    {queueError && <div className="wide-section"><p className="error-box" role="alert">{queueError}</p></div>}
    {view === 'users' && canManage(user) ? <div className="wide-section"><UsersPanel key={`${user.id}-${sessionEpoch.current}`} request={request} accounts={accounts} onRefresh={reload} /></div> : view === 'audit' && canAudit(user) ? <div className="wide-section"><AuditPanel records={audit} onRefresh={reload} onMore={() => void loadList('audit')} nextCursor={auditPage.nextCursor} busy={auditPage.busy} /></div> : <main className="main-grid">
      <div className="input-column">
        <section className="panel task-history"><div className="card-title"><h2>{user.role === 'reviewer' ? '分配给我的复核任务' : user.role === 'auditor' ? '分配给我的审计任务' : user.role === 'admin' ? '可管理任务' : '我的提交记录'}</h2><button className="ghost" onClick={reload}>刷新</button></div><p className="muted">每次加载 30 个有权访问的任务；点击加载更多查阅历史，刷新将返回第一页。</p>{!tasks.length && <p className="empty-queue">当前没有可见任务。</p>}<div className="task-list">{tasks.map(item => <button type="button" className={`history-task ${item.submission_id === taskId ? 'current' : ''}`} key={show(item.submission_id)} onClick={() => selectTask(show(item.submission_id))}><strong>{show(item.case_name)}</strong><div className="meta"><span>{statusText(item.status)}</span><span>{formatTime(item.created_at)}</span></div><div className="meta"><span>提交：{show(asData(item.owner).display_name)}</span><span>复核：{show(asData(item.reviewer).display_name)}</span></div></button>)}</div><div className="button-row">{taskPage.nextCursor && <button className="secondary" disabled={taskPage.busy} onClick={() => void loadList('tasks')}>{taskPage.busy ? '正在加载…' : '加载更多任务'}</button>}<span className="muted">{taskPage.busy ? '正在读取列表…' : `已加载 ${tasks.length} 个任务`}</span></div></section>
        {canSubmit(user) && <section className="input-panel panel"><div className="section-heading"><span className="step">01</span><div><h2>提交原始数据</h2><p>保留发言原文、回复关系和已知时间信息。</p></div></div>{healthError && <p className="error-box" role="alert">{healthError}</p>}<form onSubmit={submit}>
          <label htmlFor="case-name">任务名称</label><input id="case-name" value={caseName} onChange={event => setCaseName(event.target.value)} maxLength={120} required />
          <div className="upload-box"><div><strong>上传文本文件</strong><p>UTF-8 · TXT / JSON / JSONL / CSV · 最大 1 MiB</p></div><button type="button" className="secondary" onClick={() => fileInput.current?.click()}>选择文件</button><input ref={fileInput} type="file" accept=".txt,.json,.jsonl,.csv" hidden onChange={event => void readFile(event.target.files?.[0])} /></div>
          <div className="label-row"><label htmlFor="source-text">原始内容</label><span>文件：{filename}</span></div><textarea id="source-text" value={content} onChange={event => { setContent(event.target.value); setDemo(false); }} placeholder="粘贴发言原文，或上传包含消息列表的文件。未知身份、时间和回复关系请保留为空。" rows={14} spellCheck={false} />
          <label htmlFor="input-format">内容格式</label><select id="input-format" value={filename.split('.').pop()?.toLowerCase() || 'txt'} onChange={event => setFilename(`${filename.replace(/\.[^.]*$/, '') || 'input'}.${event.target.value}`)}><option value="txt">TXT 纯文本</option><option value="json">JSON 对话</option><option value="jsonl">JSONL 记录</option><option value="csv">CSV 表格</option></select>
          <div className="examples"><span>填充演示：</span>{EXAMPLES.map((example, index) => <button type="button" className="text-button" key={example.filename} onClick={() => { setCaseName(example.name); setFilename(example.filename); setContent(example.content); setDemo(true); }}>{index + 1}. {example.title}</button>)}</div>{demo && <p className="demo-note">当前为人工构造的演示数据，身份和相对时间均为合成内容，没有预设金标。</p>}{inputError && <p className="error-box" role="alert">{inputError}</p>}{preview && <div className="preview"><div className="meta"><strong>{preview.format} 已解析</strong><span>{preview.records} 条记录{preview.messages !== null ? ` · ${preview.messages} 条消息` : ''}</span><span>{new TextEncoder().encode(content).byteLength.toLocaleString()} 字节</span></div>{preview.snippets.slice(0, 2).map((snippet, index) => <p key={index}>{snippet}</p>)}</div>}
          <button className="primary submit-button" disabled={submitting || !preview || !!inputError || !caseName.trim()}>{submitting ? '正在提交…' : '提交到工作流'}</button><p className="fineprint">提交后由服务端调用工作流和模型。无结果、失败或上下文不足会单独显示。</p></form><details className="format-help"><summary>推荐的数据字段</summary><p>JSON 使用 messages 消息列表；每条包含 message_id、text，可选 author_key、reply_to、timestamp、relative_time_seconds。CSV 推荐相同列名。未知字段保留为空。</p></details></section>}
      </div>
      <section className="output-panel"><div className="panel result-overview"><div className="section-heading"><span className="step">02</span><div><h2>任务与研判结果</h2><p>模型观察与人工复核分别保存，可追溯材料版本。</p></div></div><form className="lookup" onSubmit={event => { event.preventDefault(); selectTask(lookupId.trim()); }}><input aria-label="查询任务编号" placeholder="输入有权访问的任务编号" value={lookupId} onChange={event => setLookupId(event.target.value)} maxLength={128} /><button className="secondary">查询任务</button></form>
        {taskId && <div className="task-toolbar"><code>{taskId}</code><div><button className="ghost" onClick={reload}>刷新</button><button className="secondary" disabled={downloadBusy || !task} onClick={() => void downloadReport()}>{downloadBusy ? '导出中…' : '导出 JSON'}</button></div></div>}{taskError && <p className="error-box" role="alert">{taskError}</p>}
        {task && <><div className="status-line"><Badge value={task.status} /><span className="muted">{lastChecked ? `最近查询 ${lastChecked}` : '等待查询任务详情'}</span></div><div className="metrics"><div><strong>{typeof task.window_count === 'number' ? task.window_count : windows.length}</strong><span>文本窗口</span></div><div><strong>{`${results} / ${jobs.length}`}</strong><span>已返回观察 / 分析任务</span></div><div><strong>{difyRuns.length}</strong><span>工作流运行记录</span></div></div>{task.status === 'waiting_model' && <p className="inline-warning">模型服务尚未就绪，任务等待恢复。当前没有模型结论。</p>}{task.status === 'pending' && <p className="muted">已接收原始数据，正在等待后端处理。</p>}{task.needs_review === true && <p className="review-banner">此任务需要人工复核。请核对逐字证据、上下文和缺失信息。</p>}{model !== null && model !== undefined && <details><summary>模型信息</summary><pre className="json-block">{JSON.stringify(model, null, 2)}</pre></details>}{task.coverage !== null && task.coverage !== undefined && <details><summary>处理覆盖情况</summary><pre className="json-block">{JSON.stringify(task.coverage, null, 2)}</pre></details>}</>}
        {!taskId && <div className="empty-state"><div className="empty-icon">↗</div><h3>选择任务查看详情</h3><p>{canSubmit(user) ? '提交文本或从记录选择任务。' : '从分配任务列表选择任务，核对原文与结果。'}</p></div>}</div>
        {jobs.map((job, index) => <ObservationCard key={show(job.id) + index} job={job} windows={windows} index={index} model={model} />)}{taskId && task && !jobs.length && <div className="panel"><p className="muted">当前没有分析任务结果。这不表示文本不存在攻击。</p></div>}
        {task && <TaskRoles key={`${user.id}-${taskId}`} task={task} taskId={taskId} user={user} accounts={accounts} request={request} onRefresh={reload} />}
        {task && asArray(task.errors).length > 0 && <div className="panel"><h3>异常信息</h3>{asArray(task.errors).map((error, index) => <pre className="error-box preserve" key={index}>{show(error)}</pre>)}</div>}
        {difyRuns.length > 0 && <details className="panel"><summary>工作流运行记录</summary>{difyRuns.map((run, index) => <div className="run-row" key={index}><code>{show(run.workflow_run_id ?? run.run_id ?? run.id)}</code><Badge value={run.state ?? run.status} /></div>)}</details>}
        {windows.length > 0 && <details className="panel"><summary>窗口原文与回复关系（{windows.length} 个窗口）</summary>{windows.map((window, index) => <div className="window" key={show(window.id) + index}><h4>窗口 {index + 1} · {show(window.id ?? window.window_id)}</h4>{asArray(window.messages).map((value, messageIndex) => { const message = asData(value); return <div className="message" key={show(message.message_id) + messageIndex}><div className="meta"><span>{show(message.message_id)}</span><span>发言者：{show(message.author_key)}</span><span>回复：{show(message.reply_to)}</span></div><p className="preserve">{show(message.text)}</p><div className="meta"><span>时间：{show(message.timestamp)}</span><span>相对时间：{show(message.relative_time_seconds)}</span></div></div>; })}</div>)}</details>}
      </section>
    </main>}
    <footer>模型输出为文本与行为线索观察，辅助人工研判；司法认定需要核实事实、上下文和证据。</footer>
  </div>;
}

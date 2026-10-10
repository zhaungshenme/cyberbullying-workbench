import { asData, type Data } from './display.ts';
import { requestErrorMessage } from './client-errors.ts';
import { AuthMutationQueue, isAuthMutation } from './auth-queue.ts';
import { ContextChanged, ContextUnavailable, expectedHeaders, requiresContext, sessionContext, type SessionSnapshot } from './session-context.ts';
import { publishSessionChange } from './session-sync.ts';
export class ApiError extends Error {
  status: number; code?: string;
  constructor(message: string, status: number, code?: string) { super(message); this.status = status; this.code = code; }
}
export class StaleResponse extends Error {}
export type RequestData = (path: string, options?: RequestInit) => Promise<Data>;
export const jsonRequest = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const authMutations = new AuthMutationQueue(8);
let authBarrier: Promise<void> = Promise.resolve();
function contextFailure(error: unknown): unknown {
  const code = error instanceof ContextUnavailable ? 'session_context_required' : error instanceof ContextChanged ? 'session_context_changed' : null;
  return code ? new ApiError(requestErrorMessage({ detail: code }, 409), 409, code) : error;
}
export function readApi(path: string, options: RequestInit = {}, captured?: SessionSnapshot): Promise<Data> {
  let snapshot: SessionSnapshot | undefined;
  try { snapshot = requiresContext(path, options.method) ? captured || sessionContext.capture() : undefined; }
  catch (error) { return Promise.reject(contextFailure(error)); }
  // Copy headers when the intent is created. A queued action must not inherit
  // caller mutation or a subsequently selected account's context.
  const initial = { ...options, headers: new Headers(options.headers) };
  const intentLineage = sessionContext.generation;
  if (isAuthMutation(path, options.method)) {
    const operation = authMutations.enqueue(async () => {
      let announced = false;
      try {
        if (intentLineage !== sessionContext.generation) throw new ContextChanged();
        const expected = snapshot ? sessionContext.resolveAuth(snapshot) : null;
        const headers = expectedHeaders(path, options.method, initial.headers, expected);
        publishSessionChange('start'); announced = true;
        const data = await fetchApi(path, { ...initial, headers, signal: undefined });
        if (path === '/api/session/password' && snapshot && expected) {
          sessionContext.recordPasswordRotation(snapshot, expected, data.session_context, asData(data.user).id);
        } else if (path === '/api/session' && options.method?.toUpperCase() === 'POST') {
          if (intentLineage !== sessionContext.generation) throw new ContextChanged();
          sessionContext.adopt(data.session_context, asData(data.user).id);
        }
        return data;
      } catch (error) { throw contextFailure(error); }
      finally { if (announced) publishSessionChange('complete'); }
    });
    authBarrier = operation.then(() => undefined, () => undefined);
    return operation;
  }
  if (path === '/api/session') return authBarrier.then(() => fetchApi(path, initial));
  try {
    const headers = expectedHeaders(path, options.method, initial.headers, snapshot?.context);
    return fetchApi(path, { ...initial, headers });
  } catch (error) { return Promise.reject(contextFailure(error)); }
}
async function fetchApi(path: string, options: RequestInit): Promise<Data> {
  const response = await fetch(path, { ...options, credentials: 'same-origin', cache: 'no-store' });
  let body: Data;
  try { body = asData(await response.json()); }
  catch { throw new ApiError('服务器返回了无法解析的响应。', response.status); }
  if (!response.ok) throw new ApiError(requestErrorMessage(body, response.status), response.status, typeof body.detail === 'string' ? body.detail : typeof body.error === 'string' ? body.error : undefined);
  return body;
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, readApi, jsonRequest } from '../lib/client-api.ts';
import { sessionContext } from '../lib/session-context.ts';
import { EXPECTED_CONTEXT_HEADER } from '../lib/request-contract.ts';
const A = 'a'.repeat(64), ROTATED = 'c'.repeat(64), B = 'b'.repeat(64);
function deferred() { let resolve!: () => void; const promise = new Promise<void>(done => { resolve = done; }); return { promise, resolve }; }
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
test('Client attaches its snapshot and refuses missing context before any business fetch', async () => {
  const original = globalThis.fetch; const seen: string[] = [];
  globalThis.fetch = async (_path, options) => { seen.push(new Headers(options?.headers).get(EXPECTED_CONTEXT_HEADER) || ''); return response({ accepted: true }); };
  try {
    sessionContext.invalidate(); sessionContext.adopt(A, 'account-a');
    await readApi('/api/workbench/analyze', jsonRequest('POST', { content: 'synthetic-material' })); assert.deepEqual(seen, [A]);
    sessionContext.invalidate(); await assert.rejects(readApi('/api/workbench/tasks?limit=30'), error => error instanceof ApiError && error.code === 'session_context_required');
    assert.equal(seen.length, 1);
  } finally { globalThis.fetch = original; sessionContext.invalidate(); }
});
test('Without a notice, changed cookie/context still rejects business, password and logout with no side effects', async () => {
  const original = globalThis.fetch; let effects = 0;
  globalThis.fetch = async (_path, options) => {
    if (new Headers(options?.headers).get(EXPECTED_CONTEXT_HEADER) !== B) return response({ detail: 'session_context_changed' }, 409);
    effects += 1; return response({ accepted: true });
  };
  try {
    sessionContext.invalidate(); sessionContext.adopt(A, 'account-a');
    for (const [path, method] of [['/api/workbench/analyze', 'POST'], ['/api/session/password', 'POST'], ['/api/session', 'DELETE']]) {
      await assert.rejects(readApi(path, jsonRequest(method, {})), error => error instanceof ApiError && error.status === 409 && error.code === 'session_context_changed');
    }
    assert.equal(effects, 0);
  } finally { globalThis.fetch = original; sessionContext.invalidate(); }
});
test('Password body completes before queued logout uses verified same-account rotation; focus GET does not steal lineage', async () => {
  const original = globalThis.fetch, body = deferred(), headers = deferred(), events: string[] = [];
  let cookieContext: string | null = A;
  globalThis.fetch = async (path, options) => {
    const expected = new Headers(options?.headers).get(EXPECTED_CONTEXT_HEADER);
    if (path === '/api/session/password') {
      assert.equal(expected, A); cookieContext = ROTATED; events.push('password-headers'); headers.resolve();
      const result = response({ user: { id: 'account-a' }, session_context: ROTATED });
      result.json = async () => { await body.promise; events.push('password-body'); return { user: { id: 'account-a' }, session_context: ROTATED }; }; return result;
    }
    if (options?.method === 'DELETE') { assert.equal(expected, ROTATED); assert.equal(cookieContext, ROTATED); cookieContext = null; events.push('logout'); return response({ authenticated: false }); }
    return response({ user: { id: 'account-a' }, session_context: cookieContext });
  };
  try {
    sessionContext.invalidate(); sessionContext.adopt(A, 'account-a');
    const password = readApi('/api/session/password', jsonRequest('POST', {})); await headers.promise;
    const focus = readApi('/api/session'); const captured = sessionContext.capture(); sessionContext.clearVisible();
    const logout = readApi('/api/session', { method: 'DELETE' }, captured);
    assert.deepEqual(events, ['password-headers']); body.resolve(); await Promise.all([password, focus, logout]);
    assert.deepEqual(events, ['password-headers', 'password-body', 'logout']); assert.equal(cookieContext, null); assert.equal(sessionContext.context, null);
  } finally { body.resolve(); globalThis.fetch = original; sessionContext.invalidate(); }
});
test('Foreign start notification breaks lineage so old queued logout never reaches the new account', async () => {
  const original = globalThis.fetch, body = deferred(), headers = deferred(); let logoutCalls = 0, cookieContext = A;
  globalThis.fetch = async (path, options) => {
    if (path === '/api/session/password') {
      cookieContext = ROTATED; headers.resolve(); const result = response({});
      result.json = async () => { await body.promise; return { user: { id: 'account-a' }, session_context: ROTATED }; }; return result;
    }
    if (options?.method === 'DELETE') logoutCalls += 1;
    return response({ authenticated: false });
  };
  try {
    sessionContext.invalidate(); sessionContext.adopt(A, 'account-a');
    const password = readApi('/api/session/password', jsonRequest('POST', {})); await headers.promise;
    const captured = sessionContext.capture(); sessionContext.clearVisible(); const logout = readApi('/api/session', { method: 'DELETE' }, captured);
    const passwordRejected = assert.rejects(password, error => error instanceof ApiError && error.code === 'session_context_changed');
    const logoutRejected = assert.rejects(logout, error => error instanceof ApiError && error.code === 'session_context_changed');
    sessionContext.invalidate(); cookieContext = B; body.resolve(); await Promise.all([passwordRejected, logoutRejected]);
    assert.equal(logoutCalls, 0); assert.equal(cookieContext, B); assert.equal(sessionContext.context, null);
  } finally { body.resolve(); globalThis.fetch = original; sessionContext.invalidate(); }
});
test('Foreign notice during a login body prevents late identity/context adoption until explicit recovery', async () => {
  const original = globalThis.fetch, body = deferred(), headers = deferred();
  globalThis.fetch = async () => {
    const result = response({}); headers.resolve();
    result.json = async () => { await body.promise; return { user: { id: 'account-a' }, session_context: A }; }; return result;
  };
  try {
    sessionContext.invalidate(); const login = readApi('/api/session', jsonRequest('POST', { username: 'synthetic-account', password: 'synthetic-input' }));
    const rejected = assert.rejects(login, error => error instanceof ApiError && error.code === 'session_context_changed');
    await headers.promise; sessionContext.invalidate(); body.resolve(); await rejected;
    assert.equal(sessionContext.context, null); assert.equal(sessionContext.userId, null);
  } finally { body.resolve(); globalThis.fetch = original; sessionContext.invalidate(); }
});

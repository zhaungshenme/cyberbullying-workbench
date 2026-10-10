import test from 'node:test';
import assert from 'node:assert/strict';
import { AuthMutationQueue, isAuthMutation } from '../lib/auth-queue.ts';
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
test('Logout waits for password response body, then revokes the rotated cookie/session', async () => {
  const queue = new AuthMutationQueue(), headers = deferred(), body = deferred(), events: string[] = [];
  let cookie = 'old-session'; const sessions = new Set([cookie]);
  const password = queue.enqueue(async () => {
    events.push('password-start'); await headers.promise;
    sessions.delete(cookie); cookie = 'rotated-session'; sessions.add(cookie); events.push('password-cookie');
    await body.promise; events.push('password-body'); return { user: 'current-account' };
  });
  const logout = queue.enqueue(async () => {
    events.push('logout-start'); assert.equal(cookie, 'rotated-session');
    sessions.delete(cookie); cookie = ''; events.push('logout-complete');
  });
  await Promise.resolve(); assert.deepEqual(events, ['password-start']);
  headers.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.deepEqual(events, ['password-start', 'password-cookie']);
  body.resolve(); await Promise.all([password, logout]);
  assert.deepEqual(events, ['password-start', 'password-cookie', 'password-body', 'logout-start', 'logout-complete']);
  assert.equal(cookie, ''); assert.equal(sessions.size, 0); assert.equal(queue.pendingCount, 0);
});
test('Failed auth mutation does not deadlock queued logout or later login', async () => {
  const queue = new AuthMutationQueue(), events: string[] = [];
  const failed = queue.enqueue(async () => { events.push('failed-password'); throw new Error('synthetic HTTP failure'); });
  const handled = assert.rejects(failed, /synthetic HTTP failure/);
  const logout = queue.enqueue(async () => { events.push('logout'); });
  const login = queue.enqueue(async () => { events.push('login'); });
  await Promise.all([handled, logout, login]);
  assert.deepEqual(events, ['failed-password', 'logout', 'login']); assert.equal(queue.pendingCount, 0);
});
test('Auth queue caps outstanding requests and recovers capacity after completion', async () => {
  const queue = new AuthMutationQueue(2), release = deferred();
  const first = queue.enqueue(async () => { await release.promise; });
  const second = queue.enqueue(async () => {});
  await assert.rejects(queue.enqueue(async () => {}), /等待当前操作/);
  assert.equal(queue.pendingCount, 2); release.resolve(); await Promise.all([first, second]);
  assert.equal(await queue.enqueue(async () => 'accepted'), 'accepted'); assert.equal(queue.pendingCount, 0);
});
test('Only cookie-changing auth routes are serialized; material reads and writes remain abortable', () => {
  assert.equal(isAuthMutation('/api/session', 'POST'), true); assert.equal(isAuthMutation('/api/session', 'delete'), true);
  assert.equal(isAuthMutation('/api/session/password', 'POST'), true);
  assert.equal(isAuthMutation('/api/session'), false); assert.equal(isAuthMutation('/api/session/password', 'DELETE'), false);
  assert.equal(isAuthMutation('/api/workbench/analyze', 'POST'), false); assert.equal(isAuthMutation('/api/workbench/tasks/task-one'), false);
});

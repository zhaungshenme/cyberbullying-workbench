import test from 'node:test';
import assert from 'node:assert/strict';
import { ContextChanged, ContextUnavailable, SessionContextState, expectedHeaders, requiresContext } from '../lib/session-context.ts';
import { EXPECTED_CONTEXT_HEADER } from '../lib/request-contract.ts';
import { NoticeTracker, parseSessionNotice } from '../lib/session-sync.ts';
const A = 'a'.repeat(64), ROTATED = 'c'.repeat(64), B = 'b'.repeat(64);
test('Every business/password/logout request needs a validated expected context; recovery and login do not', () => {
  for (const [path, method] of [['/api/workbench/tasks?limit=30', 'GET'], ['/api/workbench/analyze', 'POST'], ['/api/session/password', 'POST'], ['/api/session', 'DELETE']]) {
    assert.equal(requiresContext(path, method), true);
    assert.throws(() => expectedHeaders(path, method, undefined, null), ContextUnavailable);
    assert.equal(expectedHeaders(path, method, undefined, A).get(EXPECTED_CONTEXT_HEADER), A);
  }
  assert.equal(requiresContext('/api/session', 'GET'), false);
  assert.equal(expectedHeaders('/api/session', 'POST', { [EXPECTED_CONTEXT_HEADER]: A }).get(EXPECTED_CONTEXT_HEADER), null);
  assert.throws(() => expectedHeaders('/api/workbench/health', 'GET', undefined, 'wrong-context'), ContextUnavailable);
});
test('Only verified same-account local rotation advances a captured logout; foreign invalidation stops it', () => {
  const state = new SessionContextState(); state.adopt(A, 'account-a'); const logout = state.capture();
  state.clearVisible(); state.recordPasswordRotation(logout, A, ROTATED, 'account-a');
  assert.equal(state.resolveAuth(logout), ROTATED); assert.equal(state.context, null);
  state.invalidate(); state.adopt(B, 'account-b');
  assert.throws(() => state.resolveAuth(logout), ContextChanged);
  assert.throws(() => state.recordPasswordRotation(logout, ROTATED, A, 'account-a'), ContextChanged);
  assert.equal(state.context, B);
});
test('A late change notice invalidates an adopted session and rejects an earlier response snapshot', () => {
  const state = new SessionContextState(); state.adopt(A, 'account-a'); const old = state.capture();
  const tracker = new NoticeTracker(), now = 1791504000000;
  const notice = { type: 'workbench-session-change', event_id: '1'.repeat(32), phase: 'complete', created_at: now - 1000 };
  state.adopt(B, 'account-b'); assert.ok(tracker.accept(notice, now)); state.invalidate();
  assert.equal(state.isCurrent(old), false); assert.equal(state.context, null); assert.throws(() => state.capture(), ContextUnavailable);
  assert.equal(tracker.accept(notice, now), null);
});
test('Cross-tab signals accept only random event/change fields, with no identity, context or material', () => {
  const now = 1791504000000, notice = { type: 'workbench-session-change', event_id: '2'.repeat(32), phase: 'start', created_at: now };
  assert.ok(parseSessionNotice(notice, now));
  for (const extra of [{ session_context: A }, { user: 'account-a' }, { text: 'private-material' }, { token: 'private' }]) assert.equal(parseSessionNotice({ ...notice, ...extra }, now), null);
  assert.equal(parseSessionNotice({ ...notice, created_at: now - 301000 }, now), null);
});

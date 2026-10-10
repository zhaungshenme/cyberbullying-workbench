import test from 'node:test';
import assert from 'node:assert/strict';
import { parseUser, canSubmit, canReview, canManage, canAudit, validSessionToken, type User } from '../lib/access.ts';

const account: User = { id: 'user-one', username: 'analyst-one', display_name: '研判员一', role: 'analyst', active: true, must_change_password: false };
test('Account shaping exposes only identity and role, never session or password fields', () => {
  assert.deepEqual(parseUser({ ...account, password_hash: 'private', session_token: 'private' }), account);
  assert.equal(parseUser({ ...account, role: 'superuser' }), null);
  assert.equal(parseUser({ ...account, must_change_password: undefined }), null);
});
test('Opaque session syntax rejects old signed gateway cookies and header injection', () => {
  assert.equal(validSessionToken('A'.repeat(64)), true);
  assert.equal(validSessionToken('payload.signature'), false);
  assert.equal(validSessionToken('A'.repeat(64) + '\r\nAuthorization: public'), false);
  assert.equal(validSessionToken('short'), false);
});
test('Role views distinguish submission, administration and read-only audit', () => {
  assert.equal(canSubmit(account), true);
  assert.equal(canManage(account), false);
  assert.equal(canAudit(account), false);
  const admin: User = { ...account, role: 'admin' };
  assert.equal(canSubmit(admin), true); assert.equal(canManage(admin), true); assert.equal(canAudit(admin), true);
  assert.equal(canSubmit({ ...account, role: 'reviewer' }), false);
  assert.equal(canSubmit({ ...account, role: 'auditor' }), false);
  assert.equal(canAudit({ ...account, role: 'auditor' }), true);
});
test('Only assigned reviewers see the review form; administrators cannot self-review', () => {
  assert.equal(canReview({ ...account, role: 'reviewer' }, account.id), true);
  assert.equal(canReview({ ...account, role: 'reviewer' }, 'another-user'), false);
  assert.equal(canReview({ ...account, role: 'admin' }, account.id), false);
});
test('Forced password change and disabled accounts hide all ordinary actions', () => {
  for (const blocked of [{ ...account, role: 'admin' as const, must_change_password: true }, { ...account, role: 'admin' as const, active: false }]) {
    assert.equal(canSubmit(blocked), false); assert.equal(canManage(blocked), false); assert.equal(canAudit(blocked), false); assert.equal(canReview(blocked, blocked.id), false);
  }
  assert.equal(canSubmit(null), false);
});

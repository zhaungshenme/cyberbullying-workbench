import test from 'node:test';
import assert from 'node:assert/strict';
import { requestErrorMessage } from '../lib/client-errors.ts';
test('Authentication, account and review errors become actionable Chinese messages', () => {
  assert.equal(requestErrorMessage({ detail: 'invalid_credentials' }, 401), '账户名或密码不正确。');
  assert.match(requestErrorMessage({ detail: 'last_active_admin_protected' }, 409), /至少一个/);
  assert.match(requestErrorMessage({ detail: 'case_version_conflict' }, 409), /核对最新原文/);
  assert.match(requestErrorMessage({ detail: 'cannot_confirm_incomplete_model_coverage' }, 409), /不能确认/);
});
test('Unknown protocol errors do not expose backend codes or validation arrays', () => {
  assert.equal(requestErrorMessage({ detail: 'internal_private_key_route_failed' }, 403), '当前账户没有执行此操作的权限。');
  assert.match(requestErrorMessage({ detail: [{ loc: ['password'], input: 'secret' }] }, 422), /检查内容/);
  assert.equal(requestErrorMessage({ error: '原始数据超过 1 MiB，不会自动截断。' }, 413), '原始数据超过 1 MiB，不会自动截断。');
});

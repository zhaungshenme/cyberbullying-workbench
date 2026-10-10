import test from 'node:test';
import assert from 'node:assert/strict';
import { validContext, listQuery } from '../lib/request-contract.ts';

test('Session preconditions accept only a bounded digest, never a bearer or injection', () => {
  assert.equal(validContext('a'.repeat(64)), true);
  for (const bad of [null, 'A'.repeat(64), 'a'.repeat(63), 'Bearer ' + 'a'.repeat(64), 'a'.repeat(64) + '\r\nCookie: forged']) assert.equal(validContext(bad), false);
});
test('Pagination forwards only validated limit and opaque cursor', () => {
  assert.equal(listQuery(new URLSearchParams()), '?limit=30');
  assert.equal(listQuery(new URLSearchParams('limit=100&cursor=opaque_part.signature-1')), '?limit=100&cursor=opaque_part.signature-1');
  for (const bad of ['limit=0', 'limit=101', 'limit=1.5', 'limit=-1', 'limit=01', 'limit=2&limit=3', 'cursor=', 'cursor=a&cursor=b', 'cursor=' + 'x'.repeat(2049), 'cursor=a/b', 'actor_id=forged']) assert.equal(listQuery(new URLSearchParams(bad)), null);
});

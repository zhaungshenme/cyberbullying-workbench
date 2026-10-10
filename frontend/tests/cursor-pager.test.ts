import test from 'node:test';
import assert from 'node:assert/strict';
import { CursorPager, pagePath, validCursor } from '../lib/cursor-pager.ts';
type Item = { id: string; value: string };
test('Late load-more cannot append or move the cursor after refresh to a new first page', () => {
  const pager = new CursorPager<Item>(item => item.id), first = pager.begin(true)!;
  assert.equal(pager.accept(first, [{ id: 'old-one', value: 'old' }], 'old_next'), true);
  const oldMore = pager.begin()!; const refreshed = pager.begin(true)!;
  assert.equal(pager.accept(refreshed, [{ id: 'new-one', value: 'fresh' }], 'new_next'), true);
  assert.equal(pager.accept(oldMore, [{ id: 'old-two', value: 'late' }], 'old_after'), false);
  assert.deepEqual(pager.state.records, [{ id: 'new-one', value: 'fresh' }]); assert.equal(pager.state.nextCursor, 'new_next');
});
test('Account clearing invalidates an in-flight page and duplicate clicks cannot start concurrent pages', () => {
  const pager = new CursorPager<Item>(item => item.id), first = pager.begin(true)!;
  assert.equal(pager.begin(), null); pager.reset();
  assert.equal(pager.accept(first, [{ id: 'old-one', value: 'late' }], null), false); assert.deepEqual(pager.state.records, []);
  const current = pager.begin(true)!; pager.accept(current, [{ id: 'current', value: 'current' }], null);
  assert.equal(pager.begin(), null);
});
test('Paged append deduplicates IDs and validates next cursor before changing records', () => {
  const pager = new CursorPager<Item>(item => item.id), first = pager.begin(true)!;
  pager.accept(first, [{ id: 'one', value: 'original' }], 'next'); const more = pager.begin()!;
  assert.throws(() => pager.accept(more, [{ id: 'two', value: 'bad' }], '../private'), /无效游标/);
  assert.deepEqual(pager.state.records.map(item => item.id), ['one']);
  pager.accept(more, [{ id: 'one', value: 'duplicate' }, { id: 'two', value: 'new' }], null);
  assert.deepEqual(pager.state.records.map(item => item.id), ['one', 'two']); assert.equal(pager.state.nextCursor, null);
});
test('Requests default to 30 and reject empty, oversized or invalid cursors', () => {
  assert.equal(pagePath('/api/workbench/tasks', null), '/api/workbench/tasks?limit=30');
  assert.equal(pagePath('/api/workbench/audit', 'opaque.next_2-1'), '/api/workbench/audit?limit=30&cursor=opaque.next_2-1');
  for (const cursor of ['', 'x'.repeat(2049), 'a&admin=true', '/path']) { assert.equal(validCursor(cursor), false); assert.throws(() => pagePath('/api/workbench/tasks', cursor)); }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectInput, parseCsv, EXAMPLES } from '../lib/input.ts';
import { evidenceMatches, sourceMessage, statusText, TERMINAL, formatTime } from '../lib/display.ts';

test('CSV keeps quoted comma, line break and doubled quote', () => {
  assert.deepEqual(parseCsv('text,author_key\r\n"一句,\n另一句""引用""",A\r\n'), [['text', 'author_key'], ['一句,\n另一句"引用"', 'A']]);
});
test('Malformed CSV does not silently repair evidence', () => {
  assert.throws(() => parseCsv('text,author\n"没有结束,A'), /未闭合/);
  assert.throws(() => parseCsv('text,author\n只有一列'), /列数不一致/);
});
test('JSONL invalid line reports line and preserves unknown identity', () => {
  assert.throws(() => inspectInput('sample.jsonl', '{"text":"可读"}\n错误'), /第 2 行/);
  assert.equal(inspectInput('sample.json', '{"messages":[{"text":"求助","author_key":null}]}').messages, 1);
});
test('Examples are valid conversation inputs and empty or oversized input is rejected', () => {
  EXAMPLES.forEach(example => assert.ok(inspectInput(example.filename, example.content).messages));
  assert.throws(() => inspectInput('sample.txt', ''), /不能为空/);
  assert.throws(() => inspectInput('sample.txt', '字'.repeat(350000)), /超过 1 MiB/);
});
test('Unicode evidence aligns with codepoint offsets and missing message stays unknown', () => {
  assert.equal(evidenceMatches({ quote: '攻击', start: 1, end: 3 }, { text: '😀攻击' }), true);
  assert.equal(evidenceMatches({ quote: '攻击', start: 0, end: 2 }, { text: '😀攻击' }), false);
  assert.equal(evidenceMatches({ quote: '攻击', start: 1, end: 3 }, null), null);
  assert.equal(sourceMessage([], 'missing', 'missing'), null);
});
test('Completed with errors remains a terminal review state, not success', () => {
  assert.equal(statusText('completed_with_errors'), '完成，需复核');
  assert.ok(TERMINAL.has('completed_with_errors'));
  assert.equal(statusText('future_unknown_status'), 'future_unknown_status');
});
test('Audit time accepts server seconds and milliseconds without changing unknown values', () => {
  assert.equal(formatTime(0), '1970/1/1 08:00:00');
  assert.equal(formatTime(1791504000), formatTime(1791504000000));
  assert.equal(formatTime(null), '未提供');
  assert.equal(formatTime('unknown-time'), 'unknown-time');
});

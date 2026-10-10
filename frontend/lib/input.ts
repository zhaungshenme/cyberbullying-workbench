export const MAX_INPUT_BYTES = 1024 * 1024;
export type Preview = { format: string; records: number; messages: number | null; snippets: string[] };

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

export function parseCsv(content: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let inQuotes = false;
  let afterQuote = false;
  for (let index = 0; index < content.length; index++) {
    const char = content[index];
    if (inQuotes) {
      if (char === '"' && content[index + 1] === '"') { field += '"'; index++; }
      else if (char === '"') { inQuotes = false; afterQuote = true; }
      else field += char;
    } else if (char === '"') {
      if (field || afterQuote) throw new Error('CSV 引号位置无效。');
      inQuotes = true;
    } else if (char === ',') {
      row.push(field); field = ''; afterQuote = false;
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && content[index + 1] === '\n') index++;
      row.push(field);
      if (row.some(value => value.trim() !== '')) rows.push(row);
      row = []; field = ''; afterQuote = false;
    } else {
      if (afterQuote) throw new Error('CSV 结束引号后必须为分隔符或换行。');
      field += char;
    }
  }
  if (inQuotes) throw new Error('CSV 存在未闭合引号。');
  row.push(field);
  if (row.some(value => value.trim() !== '')) rows.push(row);
  if (!rows.length) throw new Error('CSV 内容为空。');
  const columns = rows[0].length;
  rows.forEach((value, index) => { if (value.length !== columns) throw new Error(`CSV 第 ${index + 1} 行的列数不一致。`); });
  return rows;
}

export function inspectInput(filename: string, content: string): Preview {
  if (!content.trim()) throw new Error('原始数据不能为空。');
  if (new TextEncoder().encode(content).byteLength > MAX_INPUT_BYTES) throw new Error('原始数据超过 1 MiB，不会自动截断。');
  const extension = filename.toLowerCase().split('.').pop();
  if (!['txt', 'json', 'jsonl', 'csv'].includes(extension || '')) throw new Error('只接受 UTF-8 TXT、JSON、JSONL、CSV 文件。');
  if (extension === 'txt') return { format: 'TXT', records: 1, messages: null, snippets: [content.slice(0, 240)] };
  if (extension === 'csv') {
    const rows = parseCsv(content);
    if (rows.length < 2) throw new Error('CSV 需要表头和至少一条数据。');
    const textColumn = rows[0].findIndex(value => ['text', 'content'].includes(value.trim()));
    return { format: 'CSV', records: rows.length - 1, messages: null, snippets: rows.slice(1, 4).map(row => textColumn >= 0 ? row[textColumn].slice(0, 240) : row.join(' | ').slice(0, 240)) };
  }
  const values: unknown[] = [];
  if (extension === 'jsonl') {
    content.split(/\r?\n/).forEach((line, index) => {
      if (!line.trim()) return;
      try { values.push(JSON.parse(line)); } catch { throw new Error(`JSONL 第 ${index + 1} 行无法解析。`); }
    });
  } else {
    let parsed: unknown;
    try { parsed = JSON.parse(content); } catch { throw new Error('JSON 无法解析，请检查括号、引号和逗号。'); }
    values.push(...(Array.isArray(parsed) ? parsed : [parsed]));
  }
  if (!values.length || values.some(value => !record(value))) throw new Error('JSON / JSONL 每条记录必须是对象。');
  let messages = 0;
  let countKnown = false;
  const snippets: string[] = [];
  values.forEach(value => {
    const data = record(value)!;
    if (Array.isArray(data.messages)) {
      messages += data.messages.length; countKnown = true;
      data.messages.forEach(message => {
        const item = record(message);
        if (item && typeof item.text === 'string' && snippets.length < 3) snippets.push(item.text.slice(0, 240));
      });
    } else if (typeof data.text === 'string' && snippets.length < 3) snippets.push(data.text.slice(0, 240));
  });
  return { format: extension!.toUpperCase(), records: values.length, messages: countKnown ? messages : null, snippets };
}

export const EXAMPLES = [
  {
    title: '引用求助对照',
    filename: 'demo-help.json',
    name: '合成演示 · 引用辱骂求助',
    content: JSON.stringify({
      case_id: 'demo-help', task: 'synthetic_behavior_regression', focus_message_id: 'message-001',
      messages: [{ message_id: 'message-001', text: '有人给我发“你这个废物”，我想举报，请告诉我应保留哪些记录。', author_key: null, reply_to: null, timestamp: null }],
    }, null, 2),
  },
  {
    title: '连续回复示例',
    filename: 'demo-replies.json',
    name: '合成演示 · 连续针对性回复',
    content: JSON.stringify({
      case_id: 'demo-replies', task: 'synthetic_behavior_regression', focus_message_id: null,
      messages: [
        { message_id: 'message-001', text: '我提出的是一个讨论方案。', author_key: 'actor-A', reply_to: null, timestamp: null, relative_time_seconds: 0 },
        { message_id: 'message-002', text: '你这个蠢货，少来这里发言。', author_key: 'actor-B', reply_to: 'message-001', timestamp: null, relative_time_seconds: 10 },
        { message_id: 'message-003', text: '请停止对我进行人身攻击。', author_key: 'actor-A', reply_to: 'message-002', timestamp: null, relative_time_seconds: 30 },
        { message_id: 'message-004', text: '我就说你是废物，你能怎么样？', author_key: 'actor-B', reply_to: 'message-003', timestamp: null, relative_time_seconds: 60 },
        { message_id: 'message-005', text: '废物还在吗？继续出来丢人啊。', author_key: 'actor-B', reply_to: 'message-001', timestamp: null, relative_time_seconds: 300 },
      ],
    }, null, 2),
  },
];

import type { NextRequest } from 'next/server';
import { authorize, boundedJson, fail, MAX_CONTENT_BYTES, proxy } from '@/lib/proxy';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  const denied = authorize(request, true);
  if (denied) return denied;
  try {
    const body = await boundedJson(request);
    if (!body || typeof body !== 'object' || Array.isArray(body)) return fail('提交内容必须是对象。');
    const data = body as Record<string, unknown>;
    for (const [field, limit] of [['case_name', 120], ['filename', 255], ['idempotency_key', 128]] as const) {
      if (typeof data[field] !== 'string' || !data[field].trim() || data[field].length > limit) return fail(`字段 ${field} 为空或超过长度限制。`);
    }
    if (typeof data.content !== 'string' || !data.content.trim()) return fail('原始数据不能为空。');
    if (Buffer.byteLength(data.content, 'utf8') > MAX_CONTENT_BYTES) return fail('原始数据超过 1 MiB，不会自动截断。', 413);
    return proxy(request, '/workbench/analyze', 'POST', {
      case_name: data.case_name, filename: data.filename, content: data.content, idempotency_key: data.idempotency_key,
    });
  } catch { return fail('提交内容无法解析，或超过请求大小限制。'); }
}

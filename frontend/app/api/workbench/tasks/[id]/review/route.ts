import type { NextRequest } from 'next/server';
import { authorize, proxy, boundedJson, fail, validTaskId } from '@/lib/proxy';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = authorize(request, true); if (denied) return denied;
  const { id } = await context.params; if (!validTaskId(id)) return fail('任务编号无效。');
  try {
    const data = await boundedJson(request, 12000) as Record<string, unknown>;
    if (!data || !['confirm_observation', 'request_context', 'reject_observation'].includes(String(data.outcome))
      || typeof data.comment !== 'string' || !data.comment.trim() || data.comment.length > 2000
      || typeof data.case_version !== 'number' || !Number.isInteger(data.case_version) || data.case_version < 1) return fail('复核意见须包含有效结论、1 至 2000 字评论和当前材料版本。');
    if (data.idempotency_key !== undefined && (typeof data.idempotency_key !== 'string' || !data.idempotency_key || data.idempotency_key.length > 200)) return fail('复核提交编号无效。');
    return proxy(request, `/workbench/tasks/${encodeURIComponent(id)}/review`, 'POST', { outcome: data.outcome, comment: data.comment, case_version: data.case_version, ...(data.idempotency_key === undefined ? {} : { idempotency_key: data.idempotency_key }) });
  } catch { return fail('复核请求无效。'); }
}

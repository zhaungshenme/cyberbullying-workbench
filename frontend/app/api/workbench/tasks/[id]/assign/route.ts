import type { NextRequest } from 'next/server';
import { authorize, proxy, boundedJson, fail, validTaskId } from '@/lib/proxy';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = authorize(request, true); if (denied) return denied;
  const { id } = await context.params; if (!validTaskId(id)) return fail('任务编号无效。');
  try {
    const data = await boundedJson(request, 4096) as Record<string, unknown>;
    if (!data || !['reviewer_id', 'auditor_id'].every(key => data[key] === null || (typeof data[key] === 'string' && validTaskId(data[key])))) return fail('请指定复核员和审计员，或留空解除分配。');
    return proxy(request, `/workbench/tasks/${encodeURIComponent(id)}/assign`, 'POST', { reviewer_id: data.reviewer_id, auditor_id: data.auditor_id });
  } catch { return fail('任务分配请求无效。'); }
}

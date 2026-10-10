import type { NextRequest } from 'next/server';
import { authorize, fail, proxy, validTaskId } from '@/lib/proxy';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = authorize(request);
  if (denied) return denied;
  const { id } = await context.params;
  if (!validTaskId(id)) return fail('任务编号格式无效。');
  return proxy(request, `/workbench/tasks/${encodeURIComponent(id)}/report`, 'GET', undefined, true);
}

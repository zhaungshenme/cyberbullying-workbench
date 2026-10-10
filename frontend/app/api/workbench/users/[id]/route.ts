import type { NextRequest } from 'next/server';
import { authorize, proxy, boundedJson, fail, validTaskId } from '@/lib/proxy';
import { ROLES } from '@/lib/access';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function PATCH(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const denied = authorize(request, true); if (denied) return denied;
  const { id } = await context.params; if (!validTaskId(id)) return fail('账户编号无效。');
  try {
    const data = await boundedJson(request, 4096) as Record<string, unknown>;
    if (!data || Array.isArray(data)) return fail('账户修改请求无效。');
    const change: Record<string, unknown> = {};
    if ('display_name' in data) {
      if (typeof data.display_name !== 'string' || !data.display_name.trim() || data.display_name.length > 80) return fail('姓名须为 1 至 80 字符。');
      change.display_name = data.display_name;
    }
    if ('role' in data) { if (!ROLES.includes(data.role as typeof ROLES[number])) return fail('角色无效。'); change.role = data.role; }
    if ('active' in data) { if (typeof data.active !== 'boolean') return fail('账户状态无效。'); change.active = data.active; }
    if ('password' in data) { if (typeof data.password !== 'string' || data.password.length < 12 || data.password.length > 256) return fail('临时密码须为 12 至 256 字符。'); change.password = data.password; }
    if (!Object.keys(change).length) return fail('没有有效的修改项。');
    return proxy(request, `/workbench/users/${encodeURIComponent(id)}`, 'PATCH', change);
  } catch { return fail('账户修改请求无效。'); }
}

import type { NextRequest } from 'next/server';
import { authorize, proxy, boundedJson, fail } from '@/lib/proxy';
import { ROLES } from '@/lib/access';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function GET(request: NextRequest) { return authorize(request) || proxy(request, '/workbench/users'); }
export async function POST(request: NextRequest) {
  const denied = authorize(request, true); if (denied) return denied;
  try {
    const data = await boundedJson(request, 4096) as Record<string, unknown>;
    if (!data || typeof data.username !== 'string' || !/^[A-Za-z0-9_.-]{3,64}$/.test(data.username)
      || typeof data.display_name !== 'string' || !data.display_name.trim() || data.display_name.length > 80
      || !ROLES.includes(data.role as typeof ROLES[number]) || typeof data.password !== 'string' || data.password.length < 12 || data.password.length > 256) return fail('账户信息无效：账户名须为 3 至 64 位字母、数字、点、短横线或下划线；密码至少 12 字符。');
    return proxy(request, '/workbench/users', 'POST', { username: data.username, display_name: data.display_name, role: data.role, password: data.password });
  } catch { return fail('账户创建请求无效。'); }
}

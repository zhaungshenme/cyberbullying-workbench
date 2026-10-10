import { NextResponse, type NextRequest } from 'next/server';
import { SESSION_COOKIE, cookieOptions, sessionToken } from '@/lib/session';
import { authorize, boundedJson, fail, backendRequest } from '@/lib/proxy';
import { parseUser, validSessionToken } from '@/lib/access';
import { EXPECTED_CONTEXT_HEADER, validContext } from '@/lib/request-contract';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function POST(request: NextRequest) {
  const denied = authorize(request, true);
  if (denied) return denied;
  try {
    const body = await boundedJson(request, 4096) as Record<string, unknown>;
    if (!body || typeof body.current_password !== 'string' || body.current_password.length > 256
      || typeof body.new_password !== 'string' || body.new_password.length < 12 || body.new_password.length > 256) return fail('新密码须为 12 至 256 个字符。');
    const upstream = await backendRequest('/workbench/auth/password', 'POST', { current_password: body.current_password, new_password: body.new_password }, sessionToken(request), request.headers.get(EXPECTED_CONTEXT_HEADER));
    if (!upstream.ok) return upstream;
    const data = await upstream.json();
    const user = parseUser(data.user);
    if (!validSessionToken(data.session_token) || !user || !validContext(data.session_context)) return fail('密码服务返回了无效账户信息。', 502);
    const response = NextResponse.json({ user, session_context: data.session_context }, { headers: { 'Cache-Control': 'no-store' } });
    response.cookies.set(SESSION_COOKIE, data.session_token, cookieOptions());
    return response;
  } catch { return fail('密码修改请求无效。'); }
}

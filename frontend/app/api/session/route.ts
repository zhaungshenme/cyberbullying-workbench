import { NextResponse, type NextRequest } from 'next/server';
import { SESSION_COOKIE, sameOrigin, cookieOptions, sessionToken } from '@/lib/session';
import { boundedJson, fail, backendRequest, authorize, proxy } from '@/lib/proxy';
import { parseUser, validSessionToken } from '@/lib/access';
import { EXPECTED_CONTEXT_HEADER, validContext } from '@/lib/request-contract';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
const attempts: number[] = [];

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return fail('请求来源校验失败。', 403);
  const now = Date.now();
  while (attempts[0] && attempts[0] < now - 60000) attempts.shift();
  if (attempts.length >= 30) return fail('登录尝试过于频繁，请稍后再试。', 429);
  attempts.push(now);
  try {
    const body = await boundedJson(request, 4096) as Record<string, unknown>;
    if (!body || typeof body.username !== 'string' || !body.username.trim() || body.username.length > 64
      || typeof body.password !== 'string' || !body.password || body.password.length > 256) return fail('请输入有效的账户名和密码。');
    const upstream = await backendRequest('/workbench/auth/login', 'POST', { username: body.username, password: body.password });
    if (!upstream.ok) return upstream;
    const data = await upstream.json();
    const user = parseUser(data.user);
    if (!validSessionToken(data.session_token) || !user || !validContext(data.session_context)) return fail('登录服务返回了无效账户信息。', 502);
    const response = NextResponse.json({ authenticated: true, user, session_context: data.session_context }, { headers: { 'Cache-Control': 'no-store' } });
    response.cookies.set(SESSION_COOKIE, data.session_token, cookieOptions());
    return response;
  } catch { return fail('登录请求无效。'); }
}

export async function GET(request: NextRequest) {
  return authorize(request, false, false) || proxy(request, '/workbench/auth/me', 'GET', undefined, false, false);
}

export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return fail('请求来源校验失败。', 403);
  const token = sessionToken(request);
  const denied = token ? authorize(request, true) : null;
  if (denied) return denied;
  const upstream = token ? await backendRequest('/workbench/auth/logout', 'POST', undefined, token, request.headers.get(EXPECTED_CONTEXT_HEADER)) : null;
  // If revocation fails for a transport error, retain the cookie so the user can retry.
  if (upstream && !upstream.ok && upstream.status !== 401) return upstream;
  const response = NextResponse.json({ authenticated: false }, { headers: { 'Cache-Control': 'no-store' } });
  response.cookies.set(SESSION_COOKIE, '', { ...cookieOptions(), maxAge: 0 });
  return response;
}

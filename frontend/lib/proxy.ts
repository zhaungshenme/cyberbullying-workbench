import 'server-only';
import { NextResponse, type NextRequest } from 'next/server';
import { authenticated, sameOrigin, sessionToken } from './session';
import { EXPECTED_CONTEXT_HEADER, validContext } from './request-contract';

export const MAX_CONTENT_BYTES = 1024 * 1024;
export const MAX_REQUEST_BYTES = 8 * MAX_CONTENT_BYTES;

export function fail(message: string, status = 400) {
  return NextResponse.json({ error: message }, { status, headers: { 'Cache-Control': 'no-store' } });
}

export function authorize(request: NextRequest, write = false, checkContext = true): NextResponse | null {
  if (!authenticated(request)) return fail('请先登录工作台。', 401);
  if (write && !sameOrigin(request)) return fail('请求来源校验失败。', 403);
  if (checkContext && !validContext(request.headers.get(EXPECTED_CONTEXT_HEADER)))
    return NextResponse.json({ detail: 'session_context_required' }, { status: 409, headers: { 'Cache-Control': 'no-store' } });
  return null;
}

export async function boundedJson(request: NextRequest, limit = MAX_REQUEST_BYTES): Promise<unknown> {
  if (!request.headers.get('content-type')?.startsWith('application/json')) throw new Error('请求必须使用 JSON。');
  const announced = Number(request.headers.get('content-length'));
  if (announced > limit) throw new Error('请求超过大小上限，不会自动截断。');
  const reader = request.body?.getReader();
  if (!reader) throw new Error('请求内容为空。');
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const part = await reader.read();
    if (part.done) break;
    size += part.value.byteLength;
    if (size > limit) { await reader.cancel(); throw new Error('请求超过大小上限，不会自动截断。'); }
    chunks.push(part.value);
  }
  const buffer = Buffer.concat(chunks);
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer));
}

export async function backendRequest(path: string, method = 'GET', body?: unknown, userSession?: string | null, expectedContext?: string | null): Promise<Response> {
  const token = process.env.WORKBENCH_BACKEND_TOKEN || '';
  const address = process.env.WORKBENCH_BACKEND_URL || '';
  if (!token || !address) return fail('工作台后端尚未配置。', 503);
  try {
    const base = new URL(address);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) return fail('工作台后端配置无效。', 503);
    const response = await fetch(new URL(path, base.origin), {
      method,
      cache: 'no-store',
      redirect: 'error',
      headers: { Authorization: `Bearer ${token}`, ...(userSession ? { 'X-Workbench-Session': userSession } : {}), ...(expectedContext ? { [EXPECTED_CONTEXT_HEADER]: expectedContext } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(20000),
    });
    const result = await response.text();
    const headers: Record<string, string> = { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
    return new Response(result, { status: response.status, headers });
  } catch {
    return fail('后端暂时无法连接或请求超时。任务是否已经接收，可在恢复连接后查询。', 502);
  }
}

export async function proxy(request: NextRequest, path: string, method = 'GET', body?: unknown, report = false, checkContext = true): Promise<Response> {
  const response = await backendRequest(path, method, body, sessionToken(request), checkContext ? request.headers.get(EXPECTED_CONTEXT_HEADER) : undefined);
  if (report && response.ok) response.headers.set('Content-Disposition', 'attachment; filename="analysis-report.json"');
  return response;
}

export function validTaskId(id: string): boolean {
  return /^[A-Za-z0-9_-]{1,128}$/.test(id);
}

import type { NextRequest } from 'next/server';
import { authorize, proxy, fail } from '@/lib/proxy';
import { listQuery } from '@/lib/request-contract';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function GET(request: NextRequest) {
  const denied = authorize(request); if (denied) return denied;
  const query = listQuery(request.nextUrl.searchParams);
  return query === null ? fail('分页参数无效。') : proxy(request, '/workbench/tasks' + query);
}

import type { NextRequest } from 'next/server';
import { authorize, proxy } from '@/lib/proxy';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export async function GET(request: NextRequest) {
  return authorize(request) || proxy(request, '/workbench/health');
}

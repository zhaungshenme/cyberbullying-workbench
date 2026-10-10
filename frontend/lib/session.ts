import 'server-only';
import type { NextRequest } from 'next/server';
import { validSessionToken } from './access';

// A new cookie name invalidates former shared-password gateway cookies.
export const SESSION_COOKIE = 'workbench_user_session';
export const SESSION_SECONDS = 8 * 60 * 60;
export function sessionToken(request: NextRequest): string | null {
  const value = request.cookies.get(SESSION_COOKIE)?.value;
  return validSessionToken(value) ? value! : null;
}
// Presence is only an early gate; the backend validates expiry, revocation and role.
export const authenticated = (request: NextRequest): boolean => sessionToken(request) !== null;
export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  const host = request.headers.get('host');
  if (!origin || !host) return false;
  try { return origin === new URL(`${request.nextUrl.protocol}//${host}`).origin; }
  catch { return false; }
}
export const cookieOptions = () => ({
  httpOnly: true, sameSite: 'strict' as const,
  secure: process.env.WORKBENCH_COOKIE_SECURE === 'true',
  path: '/', maxAge: SESSION_SECONDS,
});

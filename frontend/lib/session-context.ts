import { EXPECTED_CONTEXT_HEADER, validContext } from './request-contract.ts';

export type SessionSnapshot = { context: string; userId: string; lineage: number };
export class ContextUnavailable extends Error {}
export class ContextChanged extends Error {}
// Memory only. The context is a request precondition, never authentication.
export class SessionContextState {
  private value: string | null = null;
  private identity: string | null = null;
  private lineage = 0;
  private rotations = new Map<string, string>();
  get context(): string | null { return this.value; }
  get userId(): string | null { return this.identity; }
  get generation(): number { return this.lineage; }
  adopt(context: unknown, userId: unknown): void {
    if (!validContext(context) || typeof userId !== 'string' || !userId) throw new ContextUnavailable();
    if (context !== this.value || userId !== this.identity) { this.lineage += 1; this.rotations.clear(); }
    this.value = context; this.identity = userId;
  }
  capture(): SessionSnapshot {
    if (!this.value || !this.identity) throw new ContextUnavailable();
    return { context: this.value, userId: this.identity, lineage: this.lineage };
  }
  isCurrent(snapshot: SessionSnapshot): boolean {
    return snapshot.lineage === this.lineage && snapshot.context === this.value && snapshot.userId === this.identity;
  }
  clearVisible(): void { this.value = null; this.identity = null; }
  invalidate(): void { this.clearVisible(); this.lineage += 1; this.rotations.clear(); }
  resolveAuth(snapshot: SessionSnapshot): string {
    if (snapshot.lineage !== this.lineage) throw new ContextChanged();
    let context = snapshot.context;
    for (let count = 0; count < 16 && this.rotations.has(context); count += 1) context = this.rotations.get(context)!;
    return context;
  }
  recordPasswordRotation(snapshot: SessionSnapshot, expected: string, next: unknown, userId: unknown): void {
    if (snapshot.lineage !== this.lineage || userId !== snapshot.userId || !validContext(next)) throw new ContextChanged();
    // Only this tab's verified same-account password response advances a queued
    // intent. Never rebase it to a context learned from another tab or account.
    this.rotations.set(expected, next);
    if (this.rotations.size > 32) this.rotations.delete(this.rotations.keys().next().value!);
    if (this.value === expected && this.identity === userId) this.value = next;
  }
}
export const sessionContext = new SessionContextState();
export function requiresContext(path: string, method = 'GET'): boolean {
  const verb = method.toUpperCase();
  return path.startsWith('/api/workbench/') || (path === '/api/session/password' && verb === 'POST')
    || (path === '/api/session' && verb === 'DELETE');
}
export function expectedHeaders(path: string, method: string | undefined, input: HeadersInit | undefined, context?: string | null): Headers {
  const headers = new Headers(input);
  headers.delete(EXPECTED_CONTEXT_HEADER);
  if (requiresContext(path, method)) {
    if (!validContext(context)) throw new ContextUnavailable();
    headers.set(EXPECTED_CONTEXT_HEADER, context);
  }
  return headers;
}

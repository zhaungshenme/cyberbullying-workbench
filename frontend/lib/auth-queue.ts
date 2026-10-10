// Cookie-changing requests must complete (including response body processing) in
// order. Cancelling them halfway can let a late Set-Cookie undo a user's logout.
export class AuthMutationQueue {
  private tail: Promise<void> = Promise.resolve();
  private outstanding = 0;
  private readonly limit: number;
  constructor(limit = 8) { this.limit = limit; }
  get pendingCount(): number { return this.outstanding; }
  enqueue<T>(operation: () => Promise<T>): Promise<T> {
    if (this.outstanding >= this.limit) return Promise.reject(new Error('账户操作过于频繁，请等待当前操作完成。'));
    this.outstanding += 1;
    const result = this.tail.then(operation);
    // Keep the queue usable after both HTTP errors and transport failures.
    this.tail = result.then(() => undefined, () => undefined);
    return result.finally(() => { this.outstanding -= 1; });
  }
}
export function isAuthMutation(path: string, method = 'GET'): boolean {
  const verb = method.toUpperCase();
  return (path === '/api/session' && ['POST', 'DELETE'].includes(verb))
    || (path === '/api/session/password' && verb === 'POST');
}

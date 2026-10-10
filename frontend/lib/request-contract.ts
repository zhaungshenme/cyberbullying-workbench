// These values are preconditions and pagination input, never authentication.
export const EXPECTED_CONTEXT_HEADER = 'X-Workbench-Expected-Context';
export function validContext(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
}

export function listQuery(params: URLSearchParams): string | null {
  if ([...params.keys()].some(key => key !== 'limit' && key !== 'cursor')
    || params.getAll('limit').length > 1 || params.getAll('cursor').length > 1) return null;
  const limit = params.get('limit');
  const cursor = params.get('cursor');
  if (limit !== null && (!/^[1-9][0-9]{0,2}$/.test(limit) || Number(limit) > 100)) return null;
  if (cursor !== null && (!cursor || cursor.length > 2048 || !/^[A-Za-z0-9_.-]+$/.test(cursor))) return null;
  const result = new URLSearchParams();
  result.set('limit', limit || '30');
  if (cursor !== null) result.set('cursor', cursor);
  return '?' + result.toString();
}

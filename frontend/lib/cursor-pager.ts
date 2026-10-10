export type PageTicket = { generation: number; cursor: string | null; first: boolean };
export function validCursor(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= 2048 && /^[A-Za-z0-9_.-]+$/.test(value);
}
export function pagePath(path: string, cursor: string | null): string {
  if (cursor !== null && !validCursor(cursor)) throw new Error('列表游标无效，请刷新列表。');
  return `${path}?limit=30${cursor === null ? '' : `&cursor=${encodeURIComponent(cursor)}`}`;
}
export class CursorPager<T> {
  private generation = 0;
  private active: PageTicket | null = null;
  private loaded = false;
  private records: T[] = [];
  private cursor: string | null = null;
  private key: (item: T) => string;
  constructor(key: (item: T) => string) { this.key = key; }
  get state() { return { records: [...this.records], nextCursor: this.cursor, busy: this.active !== null }; }
  reset(): void { this.generation += 1; this.active = null; this.loaded = false; this.records = []; this.cursor = null; }
  begin(first = false): PageTicket | null {
    if (first) this.reset();
    if (this.active || (this.loaded && this.cursor === null)) return null;
    this.active = { generation: this.generation, cursor: this.cursor, first: !this.loaded };
    return this.active;
  }
  accept(ticket: PageTicket, records: T[], nextCursor: unknown): boolean {
    if (ticket !== this.active || ticket.generation !== this.generation) return false;
    if (nextCursor !== null && (!validCursor(nextCursor) || nextCursor === ticket.cursor)) throw new Error('列表服务返回了无效游标，请刷新后重试。');
    const keys = new Set(this.records.map(this.key));
    for (const item of records) { const key = this.key(item); if (!keys.has(key)) { this.records.push(item); keys.add(key); } }
    this.loaded = true; this.cursor = nextCursor; this.active = null; return true;
  }
  fail(ticket: PageTicket): boolean {
    if (ticket !== this.active || ticket.generation !== this.generation) return false;
    this.active = null; return true;
  }
}

export type SessionNotice = { type: 'workbench-session-change'; event_id: string; phase: 'start' | 'complete'; created_at: number };
const CHANNEL = 'workbench-session-sync-v2', STORAGE_KEY = 'workbench-session-notice-v2';
export function parseSessionNotice(value: unknown, now = Date.now()): SessionNotice | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const data = value as Record<string, unknown>;
  if (Object.keys(data).sort().join(',') !== 'created_at,event_id,phase,type' || data.type !== 'workbench-session-change'
    || typeof data.event_id !== 'string' || !/^[0-9a-f]{32}$/.test(data.event_id)
    || !['start', 'complete'].includes(String(data.phase)) || typeof data.created_at !== 'number' || !Number.isSafeInteger(data.created_at)
    || Math.abs(now - data.created_at) > 5 * 60 * 1000) return null;
  return data as SessionNotice;
}
export class NoticeTracker {
  private seen = new Set<string>();
  accept(value: unknown, now = Date.now()): SessionNotice | null {
    const notice = parseSessionNotice(value, now);
    if (!notice || this.seen.has(notice.event_id)) return null;
    this.seen.add(notice.event_id);
    if (this.seen.size > 128) this.seen.delete(this.seen.values().next().value!);
    return notice;
  }
}
let channel: BroadcastChannel | null = null;
const callbacks = new Set<(notice: SessionNotice) => void>(), tracker = new NoticeTracker();
let attached = false;
function receive(value: unknown) {
  const notice = tracker.accept(value); if (notice) callbacks.forEach(callback => callback(notice));
}
function storageChanged(event: StorageEvent) {
  if (event.key !== STORAGE_KEY || !event.newValue) return;
  try { receive(JSON.parse(event.newValue)); } catch { /* malformed signals carry no authority */ }
}
function ensureChannel() {
  if (typeof window === 'undefined') return;
  if (!attached) { window.addEventListener('storage', storageChanged); attached = true; }
  if (!channel && typeof BroadcastChannel !== 'undefined') {
    try { channel = new BroadcastChannel(CHANNEL); channel.onmessage = event => receive(event.data); } catch { channel = null; }
  }
}
export function subscribeSessionChanges(callback: (notice: SessionNotice) => void): () => void {
  ensureChannel(); callbacks.add(callback);
  return () => { callbacks.delete(callback); };
}
export function publishSessionChange(phase: SessionNotice['phase']): void {
  if (typeof window === 'undefined') return;
  ensureChannel();
  const bytes = new Uint8Array(16); globalThis.crypto.getRandomValues(bytes);
  const notice: SessionNotice = { type: 'workbench-session-change', event_id: Array.from(bytes, value => value.toString(16).padStart(2, '0')).join(''), phase, created_at: Date.now() };
  // These signals contain no account, context, credential or material content.
  try { channel?.postMessage(notice); } catch { /* storage is the fallback */ }
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(notice)); } catch { /* server preconditions remain authoritative */ }
}

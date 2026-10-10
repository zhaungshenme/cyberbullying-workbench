export const ROLES = ['admin', 'analyst', 'reviewer', 'auditor'] as const;
export type Role = typeof ROLES[number];
export type User = { id: string; username: string; display_name: string; role: Role; active: boolean; must_change_password: boolean };
export const ROLE_NAME: Record<Role, string> = { admin: '管理员', analyst: '研判员', reviewer: '复核员', auditor: '审计员' };
export const REVIEW_NAME: Record<string, string> = { pending: '等待复核', unreviewed: '尚未复核', confirm_observation: '确认观察', request_context: '要求补充上下文', reject_observation: '否定观察' };
export const validSessionToken = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{32,256}$/.test(value);
export function parseUser(value: unknown): User | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const user = value as Record<string, unknown>;
  if (typeof user.id !== 'string' || typeof user.username !== 'string' || typeof user.display_name !== 'string'
    || !ROLES.includes(user.role as Role) || typeof user.active !== 'boolean' || typeof user.must_change_password !== 'boolean') return null;
  return { id: user.id, username: user.username, display_name: user.display_name, role: user.role as Role, active: user.active, must_change_password: user.must_change_password };
}
export const canSubmit = (user: User | null): boolean => !!user?.active && !user.must_change_password && ['admin', 'analyst'].includes(user.role);
export const canManage = (user: User | null): boolean => !!user?.active && !user.must_change_password && user.role === 'admin';
export const canReview = (user: User | null, reviewerId: unknown): boolean => !!user?.active && !user.must_change_password && user.role === 'reviewer' && user.id === reviewerId;
export const canAudit = (user: User | null): boolean => !!user?.active && !user.must_change_password && ['admin', 'auditor'].includes(user.role);

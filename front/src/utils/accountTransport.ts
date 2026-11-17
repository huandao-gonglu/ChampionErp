/** 会话凭据只由 Cookie 或 Electron 主进程持有；这里仅保留内存 CSRF。 */
let csrfToken = ''
let accountUserId = ''
export const SESSION_EXPIRED_EVENT = 'champion:session-expired'

export function setCsrfToken(value: string) { csrfToken = value }
export function setAccountIdentity(value: string) { accountUserId = value }
export function businessStorageKey(value: string) { return accountUserId ? `${value}:account:${accountUserId}` : value }
export function accountHeaders(): Record<string, string> {
  return csrfToken ? { 'X-CSRF-Token': csrfToken } : {}
}
export function sessionExpired() {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT))
}

export async function accountFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers)
  for (const [name, value] of Object.entries(accountHeaders())) headers.set(name, value)
  const response = await fetch(input, { ...init, headers, credentials: 'same-origin' })
  if (response.status === 401) sessionExpired()
  return response
}

export function clearBusinessStorage() {
  try {
    const keys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
    for (const key of keys) {
      if (!key) continue
      if (key.startsWith('ai-chat-selected-conversation') || key.startsWith('draft-image-request:') || key === 'champion-erp.collect-queue.v1') {
        localStorage.removeItem(key)
      }
    }
  } catch { /* 隐私模式下仍通过整页重新载入释放所有业务状态。 */ }
}

export function accountDestination(value: unknown): string {
  return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//')
    && !value.includes('\\') && !/^\/(login|register)(?:[/?#]|$)/.test(value) ? value : '/'
}

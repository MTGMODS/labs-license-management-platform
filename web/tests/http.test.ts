import { beforeEach, expect, it, vi } from 'vitest'
import { request } from '../src/shared/api/http'
import { clearLocalSession, getTokens, setTokens } from '../src/shared/api/tokens'
import { ApiError, NetworkError, TimeoutError } from '../src/shared/api/errors'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
})

beforeEach(() => clearLocalSession())

it('serializes body and query without sending credentials to public endpoints', async () => {
  setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  const fetcher = vi.fn().mockResolvedValue(json({ ok: true }))
  vi.stubGlobal('fetch', fetcher)
  await expect(request({ service: 'usage', path: '/launch', method: 'POST', body: { mode: 'none' }, query: { a: 0, b: false, c: null, d: '' } })).resolves.toEqual({ ok: true })
  const [url, init] = fetcher.mock.calls[0]!
  expect(url).toBe('/v1/usage/launch?a=0&b=false')
  expect(init.body).toBe('{"mode":"none"}')
  expect(init.headers.Authorization).toBeUndefined()
})

it('refreshes once on 401 and retries with the new access token', async () => {
  setTokens({ accessToken: 'old', refreshToken: 'refresh' })
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({ detail: 'expired' }, 401))
    .mockResolvedValueOnce(json({ access_token: 'new', refresh_token: 'new-refresh' }))
    .mockResolvedValueOnce(json({ id: 42 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(request({ service: 'user', path: '/me', auth: true })).resolves.toEqual({ id: 42 })
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(fetcher.mock.calls[1]![0]).toBe('/v1/users/auth/refresh')
  expect(fetcher.mock.calls[2]![1].headers.Authorization).toBe('Bearer new')
  expect(getTokens()?.refreshToken).toBe('new-refresh')
})

it('shares a single refresh across simultaneous unauthorized requests', async () => {
  setTokens({ accessToken: 'old', refreshToken: 'refresh' })
  let release!: (value: Response) => void
  const refresh = new Promise<Response>((resolve) => { release = resolve })
  let refreshCalls = 0
  vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit) => {
    if (url.endsWith('/auth/refresh')) { refreshCalls++; return refresh }
    return Promise.resolve((init.headers as Record<string, string>).Authorization === 'Bearer old'
      ? json({}, 401) : json({ ok: true }))
  }))
  const requests = Array.from({ length: 5 }, () => request({ service: 'user', path: '/me', auth: true }))
  await vi.waitFor(() => expect(refreshCalls).toBe(1))
  release(json({ access_token: 'new', refresh_token: 'new-refresh' }))
  expect(await Promise.all(requests)).toEqual(Array(5).fill({ ok: true }))
  expect(refreshCalls).toBe(1)
})

it('clears tokens after explicit refresh rejection', async () => {
  setTokens({ accessToken: 'old', refreshToken: 'refresh' })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail: 'denied' }, 401)))
  await expect(request({ service: 'user', path: '/me', auth: true })).rejects.toMatchObject({ status: 401 })
  expect(getTokens()).toBeNull()
})

it('keeps tokens when refresh cannot reach the server', async () => {
  const tokens = { accessToken: 'old', refreshToken: 'refresh' }
  setTokens(tokens)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({}, 401)).mockRejectedValueOnce(new TypeError('offline')))
  await expect(request({ service: 'user', path: '/me', auth: true })).rejects.toBeInstanceOf(NetworkError)
  expect(getTokens()).toEqual(tokens)
})

it('does not loop if the refreshed access token is rejected', async () => {
  setTokens({ accessToken: 'old', refreshToken: 'refresh' })
  const fetcher = vi.fn().mockResolvedValueOnce(json({}, 401))
    .mockResolvedValueOnce(json({ access_token: 'new', refresh_token: 'new-refresh' }))
    .mockResolvedValueOnce(json({}, 401))
  vi.stubGlobal('fetch', fetcher)
  await expect(request({ service: 'user', path: '/me', auth: true })).rejects.toBeInstanceOf(ApiError)
  expect(fetcher).toHaveBeenCalledTimes(3)
})

it.each([
  [new TypeError('offline'), NetworkError],
  [new DOMException('expired', 'TimeoutError'), TimeoutError],
])('classifies transport errors: %s', async (error, expected) => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error))
  await expect(request({ service: 'usage', path: '/stats/public' })).rejects.toBeInstanceOf(expected)
})

it('preserves caller cancellation instead of reporting a timeout', async () => {
  const controller = new AbortController()
  controller.abort()
  const abort = new DOMException('cancelled', 'AbortError')
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(abort))
  await expect(request({ service: 'usage', path: '/stats/public', signal: controller.signal })).rejects.toBe(abort)
})

it('handles 204 responses without attempting JSON parsing', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
  await expect(request({ service: 'user', path: '/auth/logout', method: 'POST' })).resolves.toBeUndefined()
})

it('preserves domain error code and HTTP status', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error_code: 'ACTIVE_LICENSE_EXISTS', message: 'Confirm replacement' }, 409)))
  await expect(request({ service: 'license', path: '/activate' })).rejects.toMatchObject({ status: 409, code: 'ACTIVE_LICENSE_EXISTS', message: 'Confirm replacement' })
})

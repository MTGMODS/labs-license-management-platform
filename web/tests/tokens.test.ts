import { expect, it, vi } from 'vitest'

it('persists tokens and clears them with a single expiration notification', async () => {
  vi.resetModules()
  const tokens = await import('../src/shared/api/tokens')
  const listener = vi.fn()
  const unsubscribe = tokens.onSessionExpired(listener)
  tokens.setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  expect(window.localStorage.getItem('mtg.auth.access')).toBe('access')
  expect(tokens.getTokens()).toEqual({ accessToken: 'access', refreshToken: 'refresh' })
  tokens.clearTokens()
  expect(tokens.getTokens()).toBeNull()
  expect(window.localStorage.getItem('mtg.auth.refresh')).toBeNull()
  expect(listener).toHaveBeenCalledTimes(1)
  unsubscribe()
  tokens.clearTokens()
  expect(listener).toHaveBeenCalledTimes(1)
})

it('uses memory when localStorage is blocked by the WebView', async () => {
  vi.resetModules()
  const tokens = await import('../src/shared/api/tokens')
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  tokens.setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  expect(tokens.getTokens()).toEqual({ accessToken: 'access', refreshToken: 'refresh' })
  tokens.clearLocalSession()
  expect(tokens.getTokens()).toBeNull()
})

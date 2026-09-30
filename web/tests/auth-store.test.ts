import { beforeEach, expect, it, vi } from 'vitest'
import { useAuthStore } from '../src/features/auth/authStore'
import { clearLocalSession, getTokens, setTokens } from '../src/shared/api/tokens'
import { ApiError, NetworkError } from '../src/shared/api/errors'
import { getCurrentUser, logoutSession, type User } from '../src/shared/api/user'
import { clearUserQueries } from '../src/shared/api/queryClient'

vi.mock('../src/shared/api/user', () => ({ getCurrentUser: vi.fn(), logoutSession: vi.fn() }))
vi.mock('../src/shared/api/queryClient', () => ({ clearUserQueries: vi.fn() }))
const user = { id: 42, nickname: 'Tester', role: 'USER', status: 'ACTIVE', telegram_id: '123', discord_id: null, avatar_url: null, created_at: null } satisfies User

beforeEach(() => {
  clearLocalSession()
  vi.clearAllMocks()
  useAuthStore.setState({ status: 'initialising', user: null })
})

it('starts anonymous without stored tokens and does not call the API', async () => {
  await useAuthStore.getState().bootstrap()
  expect(useAuthStore.getState().status).toBe('anonymous')
  expect(getCurrentUser).not.toHaveBeenCalled()
})

it('coalesces repeated bootstrap calls and loads the profile', async () => {
  setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  vi.mocked(getCurrentUser).mockResolvedValue(user)
  await Promise.all([useAuthStore.getState().bootstrap(), useAuthStore.getState().bootstrap()])
  expect(getCurrentUser).toHaveBeenCalledTimes(1)
  expect(useAuthStore.getState().user).toEqual(user)
})

it('keeps a recoverable session after a network outage', async () => {
  setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  vi.mocked(getCurrentUser).mockRejectedValue(new NetworkError())
  await useAuthStore.getState().bootstrap()
  expect(useAuthStore.getState().status).toBe('authenticated')
  expect(useAuthStore.getState().user).toBeNull()
  expect(getTokens()).not.toBeNull()
})

it('clears invalid sessions and private query caches', async () => {
  setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  vi.mocked(getCurrentUser).mockRejectedValue(new ApiError({ status: 401, code: null, message: 'expired' }))
  await useAuthStore.getState().bootstrap()
  expect(useAuthStore.getState().status).toBe('anonymous')
  expect(getTokens()).toBeNull()
  expect(clearUserQueries).toHaveBeenCalled()
})

it('clears previous account caches when signing in', () => {
  useAuthStore.getState().completeSignIn({ access_token: 'access', refresh_token: 'refresh', token_type: 'bearer', user })
  expect(getTokens()?.accessToken).toBe('access')
  expect(useAuthStore.getState().user).toEqual(user)
  expect(clearUserQueries).toHaveBeenCalledOnce()
})

it('signs out locally even when server logout fails', async () => {
  setTokens({ accessToken: 'access', refreshToken: 'refresh' })
  vi.mocked(logoutSession).mockRejectedValue(new NetworkError())
  await useAuthStore.getState().signOut()
  expect(logoutSession).toHaveBeenCalledWith('refresh')
  expect(getTokens()).toBeNull()
  expect(useAuthStore.getState().status).toBe('anonymous')
})

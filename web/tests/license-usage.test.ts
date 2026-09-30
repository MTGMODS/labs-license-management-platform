import { expect, it, vi } from 'vitest'
import { request } from '../src/shared/api/http'
import { activateKey, getLicenseSalesStats, getTariffs, LICENSE_KEY_PATTERN, requestPremiumDownload, resetDevice } from '../src/shared/api/license'
import { getUsagePublicStats } from '../src/shared/api/usage'

vi.mock('../src/shared/api/http', () => ({ request: vi.fn() }))

it('reads direct license stats without status/data wrapper', async () => {
  vi.mocked(request).mockResolvedValue({ updated_at: '2026-01-01T00:00:00Z', subscriptions: { overview: { total_sold: 3 } }, forever: {} })
  const stats = await getLicenseSalesStats()
  expect(stats.updated_at).toBe('2026-01-01T00:00:00Z')
  expect(stats.subscriptions.overview.total_sold).toBe(3)
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ path: '/stats/public', service: 'license' }))
})

it('tariffs still use their separate envelope', async () => {
  vi.mocked(request).mockResolvedValue({ status: 'success', data: { plans: [] } })
  await expect(getTariffs()).resolves.toEqual({ plans: [] })
})

it('never forces license replacement unless explicitly requested', async () => {
  vi.mocked(request).mockResolvedValue({ status: 'success' })
  await activateKey('AAAA-BBBB-CCCC-DDDD')
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ auth: true, method: 'POST', body: { key: 'AAAA-BBBB-CCCC-DDDD', force: false } }))
  await activateKey('AAAA-BBBB-CCCC-DDDD', true)
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ body: { key: 'AAAA-BBBB-CCCC-DDDD', force: true } }))
})

it('protects downloads and device resets with authentication', async () => {
  await requestPremiumDownload()
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ path: '/download', auth: true, timeoutMs: 30_000 }))
  await resetDevice(42)
  expect(request).toHaveBeenLastCalledWith(expect.objectContaining({ path: '/device/42', method: 'DELETE', auth: true }))
})

it.each([['AAAA-BBBB-CCCC-DDDD', true], ['aaaa-bbbb-cccc-dddd', false], ['AAAA-BBBB', false], ['AAAA BBBB CCCC DDDD', false]])(
  'validates key format %s', (key, valid) => expect(LICENSE_KEY_PATTERN.test(key)).toBe(valid),
)

it('normalizes usage period maps and tolerates missing optional groups', async () => {
  vi.mocked(request).mockResolvedValue({ updated_at: '2026-01-01T00:00:00Z', overview: {}, distribution: { servers: [{ server: 1, users: { all_time: 12, '1h': null } }] } })
  const stats = await getUsagePublicStats()
  expect(stats.distribution.servers[0]?.users).toEqual({ all_time: 12, '30d': 0, '24h': 0, '1h': 0 })
  expect(stats.analytics.timeline.daily).toEqual([])
})

it('rejects an invalid usage payload with a typed error', async () => {
  vi.mocked(request).mockResolvedValue(null)
  await expect(getUsagePublicStats()).rejects.toMatchObject({ code: 'BAD_USAGE_PAYLOAD', status: 502 })
})

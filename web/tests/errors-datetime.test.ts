import { expect, it, vi } from 'vitest'
import { ApiError, NetworkError, TimeoutError, apiErrorTranslationKey, isNoActiveLicense, parseErrorPayload } from '../src/shared/api/errors'
import { millisecondsUntil, remainingTickMs, remainingTimeParts, utcCalendarDayKey, utcTodayKey } from '../src/shared/lib/datetime'

it.each([
  [{ error_code: 'NO_ACTIVE_LICENSE', message: 'none' }, { code: 'NO_ACTIVE_LICENSE', message: 'none' }],
  [{ detail: 'expired' }, { code: null, message: 'expired' }],
  [{ detail: [{ msg: 'required' }] }, { code: 'VALIDATION_ERROR', message: 'required' }],
  [null, { code: null, message: 'Request failed' }],
])('normalizes backend error %j', (payload, expected) => expect(parseErrorPayload(payload)).toEqual(expected))

it.each([
  [new NetworkError(), 'errors:network'], [new TimeoutError(), 'errors:timeout'],
  [new ApiError({ status: 403, code: 'NO_ACTIVE_LICENSE', message: '' }), 'errors:code.NO_ACTIVE_LICENSE'],
  [new ApiError({ status: 401, code: null, message: '' }), 'errors:unauthorized'],
  [new ApiError({ status: 429, code: 'UNKNOWN', message: '' }), 'errors:rateLimited'],
  [new Error('internal'), 'errors:unexpected'],
])('maps errors to user-facing translations', (error, expected) => expect(apiErrorTranslationKey(error)).toBe(expected))

it('does not mistake transport failures for missing VIP', () => {
  expect(isNoActiveLicense(new NetworkError())).toBe(false)
  expect(isNoActiveLicense(new ApiError({ status: 404, code: 'NO_ACTIVE_LICENSE', message: '' }))).toBe(true)
})

it.each([
  [-1, null], [0, null], [1, { count: 1, unit: 'seconds' }],
  [60_000, { count: 1, unit: 'minutes' }], [3_600_000, { count: 1, unit: 'hours' }],
  [86_400_000, { count: 1, unit: 'days' }],
])('renders remaining time at boundary %i', (ms, expected) => expect(remainingTimeParts(ms)).toEqual(expected))

it.each([[0, null], [1, 1000], [60_000, 15_000], [3_600_000, 60_000], [86_400_000, null]])(
  'selects expiry update interval at %i', (ms, expected) => expect(remainingTickMs(ms)).toBe(expected),
)

it('uses UTC calendar buckets and signed expiry intervals', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T23:59:59Z'))
  expect(utcTodayKey()).toBe('2026-01-01')
  expect(utcCalendarDayKey('2026-01-02T00:00:00Z')).toBe('2026-01-02')
  expect(millisecondsUntil('2026-01-02T00:00:00Z')).toBe(1000)
})

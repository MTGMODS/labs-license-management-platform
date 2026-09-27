import { STATS_REQUEST_TIMEOUT_MS, VIP_DOWNLOAD_TIMEOUT_MS } from '../config'
import { request } from '../http'

import type {
  ActivateKeyResult,
  DownloadRequestResult,
  LicenseDurationStat,
  LicenseForeverStats,
  LicenseInfo,
  LicensePaymentStat,
  LicensePriceStat,
  LicensePurchaseBucket,
  LicenseRetentionStats,
  LicenseSaleRow,
  LicenseSalesStats,
  LicenseSubscriptionsStats,
  LicenseTimelineDay,
  LicenseTimelineMonth,
  TariffsCatalog,
} from './types'

/** Length enforced by the backend DTO: `XXXX-XXXX-XXXX-XXXX`. */
export const LICENSE_KEY_LENGTH = 19

export const LICENSE_KEY_PATTERN = /^[A-Z0-9]{4}(-[A-Z0-9]{4}){3}$/

export function getLicenseInfo(signal?: AbortSignal): Promise<LicenseInfo> {
  return request<LicenseInfo>({ service: 'license', path: '/info', auth: true, signal })
}

/** Past licences for the signed-in user. Empty array when there is no history. */
export function getLicenseHistory(signal?: AbortSignal): Promise<LicenseInfo[]> {
  return request<LicenseInfo[]>({ service: 'license', path: '/history', auth: true, signal })
}

interface SalesStatsWire {
  updated_at: string
  subscriptions?: Record<string, unknown>
  forever?: Record<string, unknown>
}

function asFinite(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNullableString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function normalizeDuration(raw: unknown): LicenseDurationStat {
  const item = asRecord(raw)
  return {
    days: asFinite(item.duration_days ?? item.days),
    count: asFinite(item.count),
    sum: asFinite(item.sum),
    active: asFinite(item.active),
    count_share: asFinite(item.count_share),
    money_share: asFinite(item.money_share),
  }
}

function normalizePayment(raw: unknown): LicensePaymentStat {
  const item = asRecord(raw)
  return {
    method: asString(item.method, 'Unknown'),
    count: asFinite(item.count),
    sum: asFinite(item.sum),
    count_share: asFinite(item.count_share),
    money_share: asFinite(item.money_share),
  }
}

function normalizePrice(raw: unknown): LicensePriceStat {
  const item = asRecord(raw)
  return {
    price: asFinite(item.price),
    count: asFinite(item.count),
    sum: asFinite(item.sum),
    count_share: asFinite(item.count_share),
    money_share: asFinite(item.money_share),
  }
}

function normalizePurchaseBucket(raw: unknown): LicensePurchaseBucket {
  const item = asRecord(raw)
  return {
    purchases: asFinite(item.purchases),
    renewals: asFinite(item.renewals),
    users: asFinite(item.users),
    sum: asFinite(item.sum),
    share: asFinite(item.share),
  }
}

function normalizeRetention(raw: unknown): LicenseRetentionStats {
  const item = asRecord(raw)
  return {
    buyers: asFinite(item.buyers),
    repeat_buyers: asFinite(item.repeat_buyers),
    repeat_rate: asFinite(item.repeat_rate),
    avg_subscriptions_per_buyer: asFinite(item.avg_subscriptions_per_buyer),
    avg_check: asFinite(item.avg_check),
    avg_revenue_per_buyer: asFinite(item.avg_revenue_per_buyer),
    by_purchases: asArray(item.by_purchases).map(normalizePurchaseBucket),
  }
}

function normalizeTimelineDay(raw: unknown): LicenseTimelineDay {
  const item = asRecord(raw)
  return {
    date: asString(item.date),
    count: asFinite(item.count),
    sum: asFinite(item.sum),
  }
}

function normalizeTimelineMonth(raw: unknown): LicenseTimelineMonth {
  const item = asRecord(raw)
  return {
    month: asString(item.month),
    count: asFinite(item.count),
    sum: asFinite(item.sum),
  }
}

/**
 * Sale rows: display date prefers `activated_at` (purchase clock for UI),
 * while keeping both timestamps when present.
 */
function normalizeSaleRow(raw: unknown): LicenseSaleRow {
  const item = asRecord(raw)
  const activated = asNullableString(item.activated_at)
  const purchased = asNullableString(item.purchased_at)
  return {
    purchased_at: purchased,
    amount: asFinite(item.amount),
    method: asString(item.method, 'Unknown'),
    duration_days:
      item.duration_days == null ? null : asFinite(item.duration_days),
    status: asString(item.status),
    activated_at: activated,
    expires_at: asNullableString(item.expires_at),
  }
}

function emptySubscriptions(): LicenseSubscriptionsStats {
  return {
    overview: {
      total_sold: 0,
      total_money: 0,
      active: 0,
      first_activated_at: null,
      last_activated_at: null,
      avg_check: 0,
      avg_subscriptions_per_buyer: 0,
      avg_revenue_per_buyer: 0,
    },
    by_duration: [],
    by_method: [],
    retention: {
      buyers: 0,
      repeat_buyers: 0,
      repeat_rate: 0,
      avg_subscriptions_per_buyer: 0,
      avg_check: 0,
      avg_revenue_per_buyer: 0,
      by_purchases: [],
    },
    timeline: { daily: [], monthly: [] },
    sales: [],
  }
}

function emptyForever(): LicenseForeverStats {
  return {
    overview: {
      paid_sold: 0,
      total_money: 0,
      active: 0,
      avg_check: 0,
    },
    by_price: [],
    by_method: [],
  }
}

function normalizeSubscriptions(raw: unknown): LicenseSubscriptionsStats {
  const root = asRecord(raw)
  const overview = asRecord(root.overview)
  const timeline = asRecord(root.timeline)
  const empty = emptySubscriptions()

  return {
    overview: {
      total_sold: asFinite(overview.total_sold),
      total_money: asFinite(overview.total_money),
      active: asFinite(overview.active),
      first_activated_at: asNullableString(overview.first_activated_at),
      last_activated_at: asNullableString(overview.last_activated_at),
      avg_check: asFinite(overview.avg_check),
      avg_subscriptions_per_buyer: asFinite(overview.avg_subscriptions_per_buyer),
      avg_revenue_per_buyer: asFinite(overview.avg_revenue_per_buyer),
    },
    by_duration: asArray(root.by_duration).map(normalizeDuration),
    by_method: asArray(root.by_method).map(normalizePayment),
    retention: root.retention ? normalizeRetention(root.retention) : empty.retention,
    timeline: {
      daily: asArray(timeline.daily).map(normalizeTimelineDay),
      monthly: asArray(timeline.monthly).map(normalizeTimelineMonth),
    },
    sales: asArray(root.sales).map(normalizeSaleRow),
  }
}

function normalizeForever(raw: unknown): LicenseForeverStats {
  const root = asRecord(raw)
  const overview = asRecord(root.overview)

  return {
    overview: {
      /** New wire uses `paid_sold`; tolerate legacy `total_sold`. */
      paid_sold: asFinite(overview.paid_sold ?? overview.total_sold),
      total_money: asFinite(overview.total_money),
      active: asFinite(overview.active),
      avg_check: asFinite(overview.avg_check),
    },
    by_price: asArray(root.by_price).map(normalizePrice),
    by_method: asArray(root.by_method).map(normalizePayment),
  }
}

function normalizeSalesStats(raw: SalesStatsWire): LicenseSalesStats {
  return {
    updated_at: raw.updated_at,
    subscriptions: raw.subscriptions
      ? normalizeSubscriptions(raw.subscriptions)
      : emptySubscriptions(),
    forever: raw.forever ? normalizeForever(raw.forever) : emptyForever(),
  }
}

/** Public sales figures behind the VIP page. */
export async function getLicenseSalesStats(signal?: AbortSignal): Promise<LicenseSalesStats> {
  const response = await request<SalesStatsWire>({
    service: 'license',
    path: '/stats/public',
    signal,
    timeoutMs: STATS_REQUEST_TIMEOUT_MS,
  })

  return normalizeSalesStats(response)
}

export async function getTariffs(signal?: AbortSignal): Promise<TariffsCatalog> {
  const response = await request<{ status: string; data: TariffsCatalog }>({
    service: 'license',
    path: '/tariffs',
    signal,
  })
  return response.data
}

/**
 * Activates a VIP key.
 *
 * With an active licence already present the backend rejects the call with
 * 409 `ACTIVE_LICENSE_EXISTS`. Passing `force` retries destructively: the old
 * licence is expired immediately and its remaining time is lost, not carried
 * over, so callers must confirm with the user first.
 */
export function activateKey(key: string, force = false): Promise<ActivateKeyResult> {
  return request<ActivateKeyResult>({
    service: 'license',
    path: '/activate',
    method: 'POST',
    auth: true,
    body: { key, force },
  })
}

/** Asks the backend to build a personalised VIP file and return its URL. */
export function requestPremiumDownload(): Promise<DownloadRequestResult> {
  return request<DownloadRequestResult>({
    service: 'license',
    path: '/download',
    method: 'POST',
    auth: true,
    timeoutMs: VIP_DOWNLOAD_TIMEOUT_MS,
  })
}

export function resetDevice(deviceId: number): Promise<{ status: string; message: string }> {
  return request({
    service: 'license',
    path: `/device/${deviceId}`,
    method: 'DELETE',
    auth: true,
  })
}

export type {
  ActivateKeyResult,
  DownloadRequestResult,
  LicenseDetails,
  LicenseDevice,
  LicenseDurationStat,
  LicenseForeverStats,
  LicenseInfo,
  LicensePaymentStat,
  LicensePriceStat,
  LicensePurchaseBucket,
  LicenseRetentionStats,
  LicenseSaleRow,
  LicenseSalesStats,
  LicenseStatus,
  LicenseSubscriptionsStats,
  LicenseTimelineDay,
  LicenseTimelineMonth,
  LicenseTransaction,
  PaymentMethod,
  TariffPlan,
  TariffsCatalog,
} from './types'

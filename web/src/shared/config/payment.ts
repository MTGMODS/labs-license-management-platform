/**
 * Hard facts for VIP checkout: links, wallets, invoices.
 * Copy/UI strings live in i18n. Direct bank card is gated by IP country (not locale).
 */

export const VIP_BOT_URL = 'https://t.me/mtgmods_vip_bot'
export const CONTACT_URL = 'https://t.me/mtg_mods'
export const CONTACT_DISCORD_URL = 'https://discord.com/users/514135796685602827'
export const FRAGMENT_STARS_URL = 'https://fragment.com/stars/buy'
export const PAYPAL_EMAIL = 'bogdan.mtg@gmail.com'

export const VIP_BOT_START = {
  pay: `${VIP_BOT_URL}?start=pay`,
} as const

export const FUNPAY_URL = 'https://funpay.com/'
export const FUNPAY_ORDERS_URL = 'https://funpay.com/orders/'

export interface DurationOffer {
  days: number
  url: string
}

export const FUNPAY_OFFERS: readonly DurationOffer[] = [
  { days: 7, url: 'https://funpay.com/lots/offer?id=75311029' },
  { days: 30, url: 'https://funpay.com/lots/offer?id=53395293' },
  { days: 90, url: 'https://funpay.com/lots/offer?id=53392702' },
  { days: 365, url: 'https://funpay.com/lots/offer?id=65052581' },
]

export const CRYPTO_EXCHANGES = [
  { id: 'binance', label: 'Binance ID', value: '311112419', brand: 'binance' as const },
  { id: 'bybit', label: 'Bybit ID', value: '70782166', brand: 'bybit' as const },
] as const

export const CRYPTO_NETWORKS = [
  {
    id: 'ton',
    assets: 'TON, USDT',
    label: 'TON',
    address: 'UQAKaddkmi4klTHic7jOk9Z3e7qpl8Z-J9LUB6GRG5Of8NQC',
    brand: 'ton' as const,
  },
  {
    id: 'bep20',
    assets: 'BNB, USDT',
    label: 'BEP-20',
    address: '0x7fe30da48b02495cf757e33a3e404f3dad9447cb',
    brand: 'binance' as const,
  },
  {
    id: 'erc20',
    assets: 'ETH, USDC',
    label: 'ERC-20',
    address: '0x2bb655acd50304a2c91373cd4f5776367abd7fa9',
    brand: 'ethereum' as const,
  },
  {
    id: 'trc20',
    assets: 'TRON, USDT',
    label: 'TRC-20',
    address: 'TQUzuD2wA5ZCoT7zzuh3kKb5xwEizMhCpc',
    brand: 'tether' as const,
  },
] as const

/** What the buyer already has — first step of checkout. */
export type WalletId = 'card' | 'crypto' | 'stars' | 'paypal'

/**
 * Concrete checkout paths.
 * `tgStars` = buy ⭐ in Telegram with a bank card, then pay VIP in the bot.
 * `fragment` = buy ⭐ via Fragment (crypto / GRAM), then pay VIP in the bot.
 * `stars` = already have ⭐ — pay VIP in the bot directly.
 * `bank` = direct Monobank / SWIFT (hidden for RU/BY by IP).
 */
export type CheckoutRouteId =
  | 'funpay'
  | 'stars'
  | 'tgStars'
  | 'fragment'
  | 'crypto'
  | 'paypal'
  | 'bank'

export const WALLETS: readonly WalletId[] = ['card', 'crypto', 'stars', 'paypal']

/** FunPay + buy ⭐ in Telegram; bank full-width under when allowed. */
const CARD_ROUTES_BASE = ['funpay', 'tgStars'] as const satisfies readonly CheckoutRouteId[]

/** Routes shown for each starting wallet (bank injected when allowed). */
export const WALLET_ROUTES: Readonly<Record<WalletId, readonly CheckoutRouteId[]>> = {
  card: CARD_ROUTES_BASE,
  /** Fragment + FunPay side by side; direct crypto full-width under. */
  crypto: ['fragment', 'funpay', 'crypto'],
  stars: ['stars'],
  paypal: ['paypal'],
}

export function routesForWallet(
  wallet: WalletId,
  allowBankCard: boolean,
): readonly CheckoutRouteId[] {
  if (wallet === 'card' && allowBankCard) {
    return [...CARD_ROUTES_BASE, 'bank']
  }
  return WALLET_ROUTES[wallet]
}

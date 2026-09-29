import binanceUrl from '@/assets/brands/binance.svg'
import bybitUrl from '@/assets/brands/bybit.svg'
import cardUrl from '@/assets/brands/card.svg'
import ethereumUrl from '@/assets/brands/ethereum.svg'
import fragmentUrl from '@/assets/brands/fragment.svg'
import funpayUrl from '@/assets/brands/funpay.svg'
import paypalUrl from '@/assets/brands/paypal.svg'
import telegramUrl from '@/assets/brands/telegram.svg'
import tetherUrl from '@/assets/brands/tether.svg'
import tonUrl from '@/assets/brands/ton.svg'
import type { CheckoutRouteId, WalletId } from '@/shared/config/payment'

export type BrandId =
  | 'binance'
  | 'bybit'
  | 'card'
  | 'ethereum'
  | 'fragment'
  | 'funpay'
  | 'paypal'
  | 'telegram'
  | 'tether'
  | 'ton'

export const BRAND_ASSETS: Record<BrandId, string> = {
  binance: binanceUrl,
  bybit: bybitUrl,
  card: cardUrl,
  ethereum: ethereumUrl,
  fragment: fragmentUrl,
  funpay: funpayUrl,
  paypal: paypalUrl,
  telegram: telegramUrl,
  tether: tetherUrl,
  ton: tonUrl,
}

export const WALLET_BRANDS: Record<WalletId, BrandId> = {
  card: 'card',
  crypto: 'tether',
  stars: 'telegram',
  paypal: 'paypal',
}

export const ROUTE_BRANDS: Record<CheckoutRouteId, BrandId> = {
  funpay: 'funpay',
  stars: 'telegram',
  tgStars: 'telegram',
  fragment: 'fragment',
  crypto: 'tether',
  paypal: 'paypal',
  bank: 'card',
}

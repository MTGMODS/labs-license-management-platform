import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

beforeEach(() => {
  window.localStorage.clear()
  // Unexpected network calls must fail; individual tests supply fake responses.
  vi.stubGlobal('fetch', vi.fn(() => { throw new Error('Unexpected network call in test') }))
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

import { expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { RequireAuth } from '../src/app/RequireAuth'
import { RequireAdmin } from '../src/app/RequireAdmin'

const { state } = vi.hoisted(() => ({ state: {
  status: 'initialising', user: null as null | { role: string }, bootstrap: vi.fn(),
} }))
vi.mock('@/features/auth', () => ({ useAuthStore: (select: (value: typeof state) => unknown) => select(state) }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/shared/ui', () => ({
  Skeleton: () => <div>Loading</div>,
  ErrorState: ({ onRetry }: { onRetry: () => void }) => <button onClick={onRetry}>Retry</button>,
}))

function show(admin = false) {
  return render(<MemoryRouter initialEntries={['/private']}><Routes>
    <Route path="/login" element={<div>Login</div>} />
    <Route path="/dashboard" element={<div>Dashboard</div>} />
    <Route element={admin ? <RequireAdmin /> : <RequireAuth />}>
      <Route path="/private" element={<div>Private content</div>} />
    </Route>
  </Routes></MemoryRouter>)
}

it('waits for session bootstrap instead of redirecting to login', () => {
  state.status = 'initialising'; state.user = null
  show()
  expect(screen.getAllByText('Loading').length).toBeGreaterThan(0)
  expect(screen.queryByText('Login')).toBeNull()
})

it('redirects anonymous visitors', () => {
  state.status = 'anonymous'; state.user = null
  show()
  expect(screen.getByText('Login')).toBeTruthy()
  expect(screen.queryByText('Private content')).toBeNull()
})

it('offers retry when token exists but profile could not be loaded', () => {
  state.status = 'authenticated'; state.user = null
  show()
  fireEvent.click(screen.getByText('Retry'))
  expect(state.bootstrap).toHaveBeenCalled()
  expect(screen.queryByText('Private content')).toBeNull()
})

it('renders private routes for signed-in users', () => {
  state.status = 'authenticated'; state.user = { role: 'USER' }
  show()
  expect(screen.getByText('Private content')).toBeTruthy()
})

it.each(['USER', 'SMART'])('does not render admin content for %s', (role) => {
  state.user = { role }
  show(true)
  expect(screen.getByText('Dashboard')).toBeTruthy()
  expect(screen.queryByText('Private content')).toBeNull()
})

it('renders admin content for ADMIN', () => {
  state.user = { role: 'ADMIN' }
  show(true)
  expect(screen.getByText('Private content')).toBeTruthy()
})

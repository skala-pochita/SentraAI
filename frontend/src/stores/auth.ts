import { defineStore } from 'pinia'
import { get, post, tokens } from '../api/client'

export type Role = 'ADMIN' | 'DEVELOPER' | 'AUDITOR'
export interface User { id: string; email: string; name: string; role: Role }

export const useAuth = defineStore('auth', {
  state: () => ({ user: null as User | null, loaded: false }),
  getters: {
    role: (s) => s.user?.role,
    isAdmin: (s) => s.user?.role === 'ADMIN',
  },
  actions: {
    async login(email: string, password: string) {
      const res = await post<{ access_token: string; refresh_token: string; user: User }>(
        '/auth/login', { email, password })
      tokens.set(res.access_token, res.refresh_token)
      this.user = res.user
      this.loaded = true
    },
    async load() {
      if (this.loaded) return
      this.loaded = true
      if (!tokens.access) return
      try { this.user = await get<User>('/me') } catch { tokens.clear(); this.user = null }
    },
    async logout() {
      try { await post('/auth/logout', { refresh_token: tokens.refresh }) } finally {
        tokens.clear(); this.user = null
      }
    },
  },
})

import { defineStore } from 'pinia'
export const useSessionStore = defineStore('admin-session', { state: () => ({ token: sessionStorage.getItem('gamecs_admin_token') || '' }) })

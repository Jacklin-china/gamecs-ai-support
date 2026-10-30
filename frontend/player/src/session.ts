import { defineStore } from 'pinia'
export const useSessionStore = defineStore('player-session', { state: () => ({ token: sessionStorage.getItem('gamecs_player_token') || '' }) })

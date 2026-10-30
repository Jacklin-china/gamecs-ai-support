import axios from 'axios'
export const api = axios.create({ baseURL: import.meta.env.VITE_API_BASE || '/api/v1', timeout: 30000 })
api.interceptors.request.use(config => {
  const token = sessionStorage.getItem('gamecs_player_token')
  if (token && !config.headers.Authorization) config.headers.Authorization = `Bearer ${token}`
  return config
})

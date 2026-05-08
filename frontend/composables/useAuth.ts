import type { AuthResponse, User } from '~/types/api'

export function useAuth() {
  const token = useState<string>('auth-token', () => '')
  const user = useState<User | null>('auth-user', () => null)
  const initialized = useState<boolean>('auth-ready', () => false)
  const api = useApi()

  const persist = (auth: AuthResponse) => {
    token.value = auth.access_token
    user.value = auth.user
    if (process.client) {
      localStorage.setItem('auth_token', auth.access_token)
      localStorage.setItem('auth_user', JSON.stringify(auth.user))
    }
  }

  const restore = async () => {
    if (initialized.value) {
      return
    }

    if (process.client) {
      token.value = localStorage.getItem('auth_token') || ''
      const rawUser = localStorage.getItem('auth_user')
      user.value = rawUser ? JSON.parse(rawUser) : null
    }

    if (token.value) {
      try {
        user.value = await api.auth.me()
      } catch {
        logout()
      }
    }

    initialized.value = true
  }

  const login = async (username: string, password: string) => {
    const auth = await api.auth.login({ username, password })
    persist(auth)
    return auth
  }

  const register = async (username: string, password: string, admin = false) => {
    const auth = admin
      ? await api.auth.registerAdmin({ username, password })
      : await api.auth.registerUi({ username, password })
    persist(auth)
    return auth
  }

  const logout = () => {
    token.value = ''
    user.value = null
    if (process.client) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_user')
    }
  }

  return {
    token,
    user,
    initialized,
    restore,
    login,
    register,
    logout
  }
}

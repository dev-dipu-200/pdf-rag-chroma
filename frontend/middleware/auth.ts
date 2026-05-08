export default defineNuxtRouteMiddleware(async () => {
  const auth = useAuth()
  await auth.restore()

  if (!auth.token.value) {
    return navigateTo('/')
  }
})

import type { Config } from 'tailwindcss'

export default <Partial<Config>>{
  content: [
    './app.vue',
    './components/**/*.{vue,js,ts}',
    './layouts/**/*.vue',
    './pages/**/*.vue',
    './composables/**/*.{js,ts}'
  ],
  theme: {
    extend: {
      colors: {
        ink: '#08111f',
        panel: '#0f1b2d',
        line: '#1e314d',
        accent: '#f97316',
        calm: '#0ea5e9'
      },
      boxShadow: {
        panel: '0 20px 60px rgba(8, 17, 31, 0.28)'
      }
    }
  }
}

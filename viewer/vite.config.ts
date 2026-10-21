import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: { forwardConsole: false },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})

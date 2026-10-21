import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  publicDir: process.env.ISOMETRIC_SKIP_PUBLIC === '1' ? false : 'public',
  plugins: [react()],
  server: { forwardConsole: false },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})

import { defineConfig } from 'vitest/config'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  // svelte plugin compiles `*.svelte.test.ts` rune modules; the `browser`
  // resolve condition forces the client build so `$effect`/`$state` actually run
  // under vitest (the default SSR build makes effects no-ops). Rune tests opt into
  // jsdom per-file via `@vitest-environment jsdom`; plain tests stay on node.
  plugins: [svelte()],
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  test: {
    environment: 'node',
    include: ['src/**/*.{test,spec}.ts'],
  },
})

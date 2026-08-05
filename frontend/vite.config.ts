import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // Percorsi relativi: necessari perché Electron carica da file://
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'chrome120',
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});

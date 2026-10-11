import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev, forward API + short-link calls to the local Flask server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:5000',
      '/go': 'http://localhost:5000',
    },
  },
});

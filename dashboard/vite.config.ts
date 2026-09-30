import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server on :5173 proxies the API and WebSockets to the Python server on :8000.
// In production the Python server serves dashboard/dist itself (DECISIONS.md D16).
const SERVER = "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": SERVER,
      "/ws": { target: SERVER, ws: true },
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// TODO(P5/P6): proxy /api and /ws to the backend gateway during local dev
// (LLD §5, HLD §5.1 endpoints) so the frontend doesn't need CORS in dev mode.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: true,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});

import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_URL || "http://localhost:8080",
        changeOrigin: true,
        // ws:true lets /api/ws/live proxy WebSocket upgrades through to
        // FastAPI's WS endpoint. Without this, the client's
        // new WebSocket("ws://.../api/ws/live") would fail with 502.
        ws: true,
      },
      // /media serves Imagen-generated PNGs the LOCAL_DEV image agent
      // writes to drafts/media/<telemetry_id>/<id>.png. The FastAPI app
      // mounts this directory at /media; the proxy lets <img src="/media/...">
      // resolve through Vite's dev server.
      "/media": {
        target: process.env.VITE_API_URL || "http://localhost:8080",
        changeOrigin: true,
      },
    },
  },
});

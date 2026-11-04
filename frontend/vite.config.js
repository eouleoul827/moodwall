import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@clients": fileURLToPath(new URL("../clients", import.meta.url)) },
  },
  server: {
    proxy: {
      // The event stream sends keepalives every 15 seconds. Keep it open;
      // ordinary JSON requests below still have a bounded proxy timeout.
      "/api/display/events": {
        target: process.env.MOODWALL_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: false,
        timeout: 0,
        proxyTimeout: 0,
      },
      "/api": {
        target: process.env.MOODWALL_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: false,
        timeout: 10_000,
        proxyTimeout: 10_000,
      },
    },
    fs: { allow: [fileURLToPath(new URL("..", import.meta.url))] },
  },
  build: { target: "es2022" },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.js"],
    include: ["src/**/*.test.{js,jsx}"],
    restoreMocks: true,
    clearMocks: true,
    maxWorkers: 2,
  },
});

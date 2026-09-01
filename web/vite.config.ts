import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发期 /api 与 /files 反代到本机 FastAPI（生产由 Nginx 同源部署）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/files": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});

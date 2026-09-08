import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/zrender")) return "chart-renderer";
          if (id.includes("node_modules/echarts")) return "charts";
          if (id.includes("node_modules/lucide-react")) return "icons";
          if (id.includes("node_modules/react") || id.includes("node_modules/react-router")) return "react";
          return undefined;
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": process.env.VITE_PROXY_TARGET || "http://localhost:8001",
    },
  },
});

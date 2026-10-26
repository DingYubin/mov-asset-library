import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  base: "/",
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": "http://127.0.0.1:8800",
    },
  },
}));

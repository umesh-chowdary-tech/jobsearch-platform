import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the page on :5173 and passes /api and /files to the
// service (`jobsearch hosted --dev` on :8000), so the browser sees one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000", "/files": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist", sourcemap: false },
});

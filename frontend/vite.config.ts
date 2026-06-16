import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets live under Flask's /static so its existing static handler serves
// them; Flask's catch-all returns this index.html for every SPA route.
export default defineConfig({
  plugins: [react()],
  base: "/static/spa/",
  build: {
    outDir: "../app/static/spa",
    emptyOutDir: true,
  },
  server: {
    // Dev server proxies the API + asset routes to the Flask backend.
    proxy: {
      "/api": "http://localhost:8080",
      "/art": "http://localhost:8080",
      "/manifest.webmanifest": "http://localhost:8080",
      "/sw.js": "http://localhost:8080",
    },
  },
});

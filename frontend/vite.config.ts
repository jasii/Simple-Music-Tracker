import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets live under Flask's /static so its existing static handler serves
// them; Flask's catch-all returns this index.html for every SPA route.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Dev server runs at root so BrowserRouter (no basename) matches; the
  // production build keeps the /static/spa/ base for Flask's static handler.
  base: command === "serve" ? "/" : "/static/spa/",
  build: {
    outDir: "../app/static/spa",
    emptyOutDir: true,
  },
  server: {
    // Dev server proxies the API + asset routes to the Flask backend.
    proxy: {
      "/api": "http://localhost:8080",
      "/art": "http://localhost:8080",
      // Static assets (vinyl.svg placeholder, service icons) live in app/static.
      "/static": "http://localhost:8080",
      "/manifest.webmanifest": "http://localhost:8080",
      "/sw.js": "http://localhost:8080",
    },
  },
}));

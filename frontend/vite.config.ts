import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Built assets live under Flask's /static so its existing static handler serves
// them; Flask's catch-all returns this index.html for every SPA route.
export default defineConfig(({ command }) => ({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // Dev server runs at root so BrowserRouter (no basename) matches; the
  // production build keeps the /static/spa/ base for Flask's static handler.
  base: command === "serve" ? "/" : "/static/spa/",
  build: {
    outDir: "../app/static/spa",
    emptyOutDir: true,
  },
  server: {
    // Listen on every interface, not just loopback, so the dev UI can be
    // opened from a phone on the same network (and over Tailscale).
    host: true,
    strictPort: true,
    // The page is reached by IP or hostname rather than "localhost", and Vite
    // blocks unknown Host headers by default.
    allowedHosts: true,
    hmr: {
      // The websocket has to go back to whatever host the page was loaded
      // from, not to localhost, or hot reload silently dies on the phone.
      clientPort: 5173,
    },
    // Dev server proxies the API + asset routes to the Flask backend.
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/art": "http://127.0.0.1:8080",
      // Static assets (vinyl.svg placeholder, service icons) live in app/static.
      "/static": "http://127.0.0.1:8080",
      "/manifest.webmanifest": "http://127.0.0.1:8080",
      "/sw.js": "http://127.0.0.1:8080",
    },
  },
}));

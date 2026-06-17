import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Provider } from "./components/ui/provider";
import { Toaster } from "./components/ui/toaster";
import App from "./App";
import "./global.css";

// Cache server reads so navigating back to a page paints instantly from cache
// (stale-while-revalidate) instead of blanking to a "Loading..." flash.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

// Register the service worker for the PWA/offline shell (mirrors old app.js).
// Skip in dev: the SW is cache-first and serves stale Vite modules → blank page.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
} else if (!import.meta.env.PROD && "serviceWorker" in navigator) {
  // Tear down any SW + caches a previous dev session registered.
  navigator.serviceWorker.getRegistrations().then((rs) => rs.forEach((r) => r.unregister()));
  if (window.caches) caches.keys().then((ks) => ks.forEach((k) => caches.delete(k)));
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Provider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
        <Toaster />
      </Provider>
    </QueryClientProvider>
  </StrictMode>,
);

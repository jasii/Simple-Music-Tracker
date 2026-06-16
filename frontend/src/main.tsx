import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Provider } from "./components/ui/provider";
import { Toaster } from "./components/ui/toaster";
import App from "./App";
import "./global.css";

// Register the service worker for the PWA/offline shell (mirrors old app.js).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Provider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
      <Toaster />
    </Provider>
  </StrictMode>,
);

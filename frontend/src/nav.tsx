import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type { NavConfig } from "./types";

const NavContext = createContext<NavConfig | null>(null);

const FALLBACK: NavConfig = {
  items: [
    { key: "upcoming", endpoint: "upcoming_page", label: "Upcoming", path: "/upcoming", hidden: false },
    { key: "artists", endpoint: "artists_page", label: "Artists", path: "/artists", hidden: false },
    { key: "following", endpoint: "subscriptions_page", label: "Following", path: "/subscriptions", hidden: false },
    { key: "discover", endpoint: "discover_page", label: "Discover", path: "/discover", hidden: false },
    { key: "ignored", endpoint: "ignored_page", label: "Ignored", path: "/ignored", hidden: false },
    { key: "settings", endpoint: "settings_page", label: "Settings", path: "/settings", hidden: false },
  ],
  home: "upcoming",
  home_path: "/upcoming",
  default_theme: "dark",
  hide_page_descriptions: false,
};

export function NavProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<NavConfig | null>(null);
  useEffect(() => {
    api.nav().then(setConfig).catch(() => setConfig(FALLBACK));
  }, []);
  return <NavContext.Provider value={config}>{children}</NavContext.Provider>;
}

export function useNav(): NavConfig {
  return useContext(NavContext) ?? FALLBACK;
}

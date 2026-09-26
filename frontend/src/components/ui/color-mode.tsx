import { useTheme } from "next-themes";
import * as React from "react";
import { LuMoon, LuSun } from "react-icons/lu";
import { Button } from "./button";

export type ColorMode = "light" | "dark";

export function useColorMode() {
  const { resolvedTheme, setTheme } = useTheme();
  const toggleColorMode = () => setTheme(resolvedTheme === "dark" ? "light" : "dark");
  return { colorMode: resolvedTheme as ColorMode, setColorMode: setTheme, toggleColorMode };
}

export function useColorModeValue<T>(light: T, dark: T) {
  const { colorMode } = useColorMode();
  return colorMode === "dark" ? dark : light;
}

export function ColorModeIcon() {
  const { colorMode } = useColorMode();
  return colorMode === "dark" ? <LuMoon /> : <LuSun />;
}

// Guard the icon behind a mount flag: next-themes can't know the resolved theme
// during SSR/first paint, so render nothing until mounted to avoid a flash.
export function ColorModeButton() {
  const { toggleColorMode } = useColorMode();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  return (
    <Button variant="ghost" size="icon-sm" aria-label="Toggle color mode" onClick={toggleColorMode}>
      {mounted ? <ColorModeIcon /> : null}
    </Button>
  );
}

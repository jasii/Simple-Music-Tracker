import { ThemeProvider, type ThemeProviderProps } from "next-themes";

export function Provider(props: ThemeProviderProps) {
  return (
    <ThemeProvider attribute="class" defaultTheme="dark" disableTransitionOnChange {...props} />
  );
}

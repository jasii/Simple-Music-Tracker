// Floating selection bar for tables, driven by TanStack row-selection state
// (the page passes the selected count + a clear handler).
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { LuX } from "react-icons/lu";
import { Button } from "./ui/button";
import { Separator } from "./ui/separator";

export function TableActionBar({
  open,
  count,
  onClear,
  label = "selected",
  children,
}: {
  open: boolean;
  count: number;
  onClear: () => void;
  label?: string;
  children: ReactNode;
}) {
  if (!open) return null;
  return createPortal(
    <div className="fixed bottom-6 left-1/2 z-50 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-lg border bg-popover px-3 py-2 shadow-lg">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium whitespace-nowrap">{count} {label}</span>
        <Separator orientation="vertical" className="h-5" />
        {children}
        <Button variant="ghost" size="icon-sm" aria-label="Clear selection" onClick={onClear}>
          <LuX />
        </Button>
      </div>
    </div>,
    document.body,
  );
}

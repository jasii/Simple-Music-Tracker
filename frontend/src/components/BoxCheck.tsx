import { Checkbox } from "./ui/checkbox";

// A compact, accessible checkbox. Shared by the Artists table and the
// discography table.
export function BoxCheck({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <Checkbox
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onCheckedChange={(v) => onChange(v === true)}
    />
  );
}

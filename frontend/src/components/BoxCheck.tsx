import { Checkbox } from "@chakra-ui/react";

// A compact, accessible checkbox with a hidden-input label. Shared by the
// Artists table and the discography table.
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
    <Checkbox.Root size="sm" checked={checked} disabled={disabled} onCheckedChange={(e) => onChange(!!e.checked)}>
      <Checkbox.HiddenInput aria-label={label} />
      <Checkbox.Control />
    </Checkbox.Root>
  );
}

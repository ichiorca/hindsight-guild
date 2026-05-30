import { useEffect, useState } from "react";
import { Button, type ButtonProps } from "@/components/ui/Button";

interface ConfirmButtonProps extends Omit<ButtonProps, "onClick"> {
  /** Fires only after the two-step confirm. */
  onConfirm: () => void;
  /** Label shown in the armed/confirm state. */
  confirmLabel?: string;
}

/**
 * Inline two-step confirm for irreversible actions — no modal. First click
 * arms it (swaps to "Confirm" + Cancel); a second click within 4s fires.
 * Used for outward-facing / fleet-wide actions (publish, roll out a playbook
 * to every agent, sync to disk) so they can't go off on a single stray click.
 */
export function ConfirmButton({
  onConfirm,
  confirmLabel = "Confirm",
  children,
  variant,
  size,
  disabled,
  className,
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);

  if (armed) {
    return (
      <span className="inline-flex items-center gap-1">
        <Button
          variant={variant ?? "destructive"}
          size={size}
          disabled={disabled}
          onClick={() => { setArmed(false); onConfirm(); }}
        >
          {confirmLabel}
        </Button>
        <Button variant="ghost" size={size} onClick={() => setArmed(false)}>
          Cancel
        </Button>
      </span>
    );
  }

  return (
    <Button
      variant={variant}
      size={size}
      disabled={disabled}
      className={className}
      onClick={() => setArmed(true)}
    >
      {children}
    </Button>
  );
}

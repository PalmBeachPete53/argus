import { useEffect, useRef, useState } from "react";
import { formatDateInput, maskDateInput } from "../lib/dateInput";
import DatePicker from "./DatePicker";

interface DateFieldProps {
  label: string;
  /** Masked display text (`DD/MM/YYYY`), controlled by the parent. */
  value: string;
  onChange: (text: string) => void;
  disabled?: boolean;
  /** Render the field with the error border (impossible date typed). */
  error?: boolean;
}

/**
 * A Discovery date field: a masked text input (`DD/MM/YYYY`, slashes optional)
 * plus a calendar toggle. Keyboard and calendar both report the same internal
 * value because display text always round-trips through `formatDateInput`.
 *
 * The input is plain text so typing is never hostage to the OS locale; the
 * native `input[type="date"]` is not used (its format is not overridable).
 */
export default function DateField({ label, value, onChange, disabled, error }: DateFieldProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  return (
    <div ref={rootRef} className={`date-field-wrap${error ? " date-field-error" : ""}`}>
      <label className="discovery-range">
        <span>{label}</span>
        <div className="date-field">
          <input
            className="date-field-input"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            placeholder="DD/MM/YYYY"
            value={value}
            onChange={(e) => onChange(maskDateInput(e.target.value))}
            disabled={disabled}
          />
          <button
            type="button"
            className="date-field-toggle"
            aria-label={`Open calendar for ${label}`}
            disabled={disabled}
            onClick={() => setOpen((v) => !v)}
          >
            📅
          </button>
        </div>
      </label>
      {open && !disabled && (
        <div className="date-picker-anchor">
          <DatePicker
            value={value}
            onSelect={(iso) => {
              onChange(formatDateInput(iso));
              setOpen(false);
            }}
          />
        </div>
      )}
    </div>
  );
}
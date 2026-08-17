import { useState } from "react";
import { FR_MONTHS, FR_WEEKDAYS, monthGrid, parseDateInput, toIsoDate } from "../lib/dateInput";

interface DatePickerProps {
  /** Current ISO `YYYY-MM-DD` (or display text the picker can parse). */
  value: string;
  onSelect: (iso: string) => void;
}

/**
 * Custom calendar popup (WKWebView has no reliable native date picker, so ours
 * is hand-rolled). The selected day is handed back as ISO — the exact same
 * value keyboard entry produces, because both paths share `toIsoDate` /
 * `parseDateInput`.
 */
export default function DatePicker({ value, onSelect }: DatePickerProps) {
  const parsed = parseDateInput(value);
  const today = new Date();
  const [view, setView] = useState(() => {
    if (parsed) {
      const [y, m] = parsed.split("-").map(Number);
      return { year: y, month: m }; // 1-12
    }
    return { year: today.getFullYear(), month: today.getMonth() + 1 };
  });

  const { year, month } = view;
  const cells = monthGrid(year, month);

  const shift = (delta: number): void => {
    let y = year;
    let m = month + delta;
    if (m < 1) {
      m = 12;
      y -= 1;
    } else if (m > 12) {
      m = 1;
      y += 1;
    }
    setView({ year: y, month: m });
  };

  const isToday = (day: number): boolean =>
    day === today.getDate() && month === today.getMonth() + 1 && year === today.getFullYear();

  return (
    <div className="date-picker" role="dialog" aria-label="Date picker">
      <div className="date-picker-head">
        <button type="button" className="date-picker-nav" aria-label="Previous month" onClick={() => shift(-1)}>
          ‹
        </button>
        <span className="date-picker-title">
          {FR_MONTHS[month - 1]} {year}
        </span>
        <button type="button" className="date-picker-nav" aria-label="Next month" onClick={() => shift(1)}>
          ›
        </button>
      </div>
      <div className="date-picker-weekdays" aria-hidden="true">
        {FR_WEEKDAYS.map((d) => (
          <span key={d}>{d}</span>
        ))}
      </div>
      <div className="date-picker-grid">
        {cells.map((day, index) =>
          day === null ? (
            <span key={index} className="date-picker-cell empty" />
          ) : (
            <button
              key={index}
              type="button"
              className={`date-picker-cell${isToday(day) ? " today" : ""}${
                toIsoDate(year, month, day) === parsed ? " selected" : ""
              }`}
              onClick={() => onSelect(toIsoDate(year, month, day))}
            >
              {day}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
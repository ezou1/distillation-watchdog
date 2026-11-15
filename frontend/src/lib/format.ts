const EMPTY = "–";

export function formatNumber(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EMPTY;
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  const magnitude = Math.abs(value);
  if (magnitude !== 0 && (magnitude < 0.001 || magnitude >= 1e6)) return value.toExponential(2);
  return value.toLocaleString("en-US", { maximumFractionDigits: magnitude < 1 ? 4 : 2 });
}

/** Large counters in the status bar: 12,345 stays exact, 1,234,567 becomes 1.23M. */
export function formatCount(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return EMPTY;
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  return Math.round(value).toLocaleString("en-US");
}

export function formatScore(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : EMPTY;
}

export function formatPercent(value: unknown, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : EMPTY;
}

export function formatSigned(value: number, digits = 2): string {
  const text = Math.abs(value).toFixed(digits);
  return value >= 0 ? `+${text}` : `−${text}`;
}

function simDate(seconds: unknown): Date | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Clock time (UTC) of a simulation timestamp: 14:05:09 */
export function formatSimTime(seconds: unknown): string {
  return simDate(seconds)?.toISOString().slice(11, 19) ?? "";
}

/** Date and clock time (UTC) of a simulation timestamp: 2026-01-05 14:05:09 */
export function formatSimDateTime(seconds: unknown): string {
  return simDate(seconds)?.toISOString().replace("T", " ").slice(0, 19) ?? "";
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** "request_auc" -> "Request auc". For report keys that have no hand-written label. */
export function humanizeKey(key: string): string {
  const words = key
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b(auc|fpr|cpu|rss|llm|usd|roc|pr)\b/gi, (word) => word.toUpperCase());
  return words.charAt(0).toUpperCase() + words.slice(1);
}

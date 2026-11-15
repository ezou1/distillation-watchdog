// The request-rate history of the board chart.
//
// The watchdog keeps only the last 240 buckets of 60 simulated seconds (4 simulated hours). The chart has
// windows of up to 30 simulated days, so the page keeps its own history: it merges each summary into this
// store and saves the store in the browser. Recent data stays in 1-minute buckets. Older data is combined
// into 1-hour buckets, so the store stays small.

import type { Summary } from "@/api";

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;
const FINE_KEEP_S = 48 * HOUR;
const COARSE_KEEP_S = 30 * DAY;
const STORAGE_KEY = "baker-rate-v1";
const SAVE_EVERY_MS = 5000;

export interface RateWindow {
  id: string;
  label: string;
  /** Length of the window, in simulated seconds. */
  span: number;
  /** Width of one drawn point, in simulated seconds. */
  bin: number;
}

export const WINDOWS: readonly RateWindow[] = [
  { id: "1h", label: "1h", span: HOUR, bin: MINUTE },
  { id: "24h", label: "24h", span: 24 * HOUR, bin: 10 * MINUTE },
  { id: "48h", label: "48h", span: 48 * HOUR, bin: 20 * MINUTE },
  { id: "1w", label: "1w", span: 7 * DAY, bin: HOUR },
  { id: "1m", label: "1m", span: 30 * DAY, bin: 6 * HOUR },
];

/** [served, blocked] requests in one bucket. */
type Counts = [number, number];

export interface RateBin {
  /** Middle of the bin, in simulated seconds. */
  t: number;
  /** Requests in each simulated minute. Null where the page has no data. */
  served: number | null;
  blocked: number | null;
}

interface Saved {
  fine: Array<[number, number, number]>;
  hour: Array<[number, number, number]>;
  simTime: number;
  total: number;
}

export class RateStore {
  /** Bucket start (simulated seconds) to counts. 1-minute buckets, the last 48 simulated hours. */
  private fine = new Map<number, Counts>();
  /** 1-hour buckets of the data that left the fine store. */
  private hour = new Map<number, Counts>();
  private simTime = 0;
  private total = 0;
  private savedAt = 0;

  constructor() {
    this.load();
  }

  /** Merge one summary. Returns true when the store changed. */
  add(summary: Summary): boolean {
    const total = summary.served + summary.blocked;
    // A reset of the watchdog sets the counters to zero and the clock back: the old history is of another run.
    if (total < this.total || summary.sim_time < this.simTime - HOUR) this.clear();
    this.total = total;
    this.simTime = Math.max(this.simTime, summary.sim_time);
    for (const point of summary.rate_history ?? []) {
      // The newest value of a bucket replaces the old one: the current bucket still grows.
      this.fine.set(point.t, [point.served, point.blocked]);
    }
    this.fold();
    this.save(false);
    return true;
  }

  /** The newest simulated time the store knows. */
  now(): number {
    return this.simTime;
  }

  /** The points of one window that ends at `end`. */
  bins(window: RateWindow, end: number): RateBin[] {
    const count = Math.round(window.span / window.bin);
    // Align the bins to the bin size so a point does not change its value while the window moves.
    const last = Math.floor(end / window.bin) * window.bin;
    const first = last - (count - 1) * window.bin;
    const sums: Array<Counts | null> = Array.from({ length: count }, () => null);
    let earliest = Infinity;
    const put = (t: number, width: number, counts: Counts) => {
      earliest = Math.min(earliest, t);
      const index = Math.floor((t - first) / window.bin);
      if (index < 0 || index >= count || width > window.bin) return;
      const sum = (sums[index] ??= [0, 0]);
      sum[0] += counts[0];
      sum[1] += counts[1];
    };
    for (const [t, counts] of this.hour) put(t, HOUR, counts);
    for (const [t, counts] of this.fine) put(t, MINUTE, counts);

    return sums.map((sum, index) => {
      const start = first + index * window.bin;
      // Before the first known data there is nothing to show. After it, an empty bin means zero requests.
      if (start + window.bin <= earliest || start > end) return { t: start + window.bin / 2, served: null, blocked: null };
      // The newest bin is not complete: divide by the minutes that passed.
      const minutes = Math.max(1, Math.min(window.bin, end - start) / MINUTE);
      return {
        t: start + window.bin / 2,
        served: (sum?.[0] ?? 0) / minutes,
        blocked: (sum?.[1] ?? 0) / minutes,
      };
    });
  }

  clear(): void {
    this.fine.clear();
    this.hour.clear();
    this.simTime = 0;
    this.total = 0;
  }

  /** Move 1-minute buckets that are older than 48 simulated hours into 1-hour buckets. */
  private fold(): void {
    const fineFrom = this.simTime - FINE_KEEP_S;
    for (const [t, counts] of this.fine) {
      if (t >= fineFrom) continue;
      const key = Math.floor(t / HOUR) * HOUR;
      const sum = this.hour.get(key) ?? [0, 0];
      this.hour.set(key, [sum[0] + counts[0], sum[1] + counts[1]]);
      this.fine.delete(t);
    }
    const coarseFrom = this.simTime - COARSE_KEEP_S;
    for (const t of this.hour.keys()) if (t < coarseFrom) this.hour.delete(t);
  }

  save(now: boolean): void {
    if (!now && Date.now() - this.savedAt < SAVE_EVERY_MS) return;
    this.savedAt = Date.now();
    const saved: Saved = {
      fine: [...this.fine].map(([t, [served, blocked]]) => [t, served, blocked]),
      hour: [...this.hour].map(([t, [served, blocked]]) => [t, served, blocked]),
      simTime: this.simTime,
      total: this.total,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage can be blocked or full. The chart still works for this visit.
    }
  }

  private load(): void {
    try {
      const text = localStorage.getItem(STORAGE_KEY);
      if (!text) return;
      const saved = JSON.parse(text) as Partial<Saved>;
      for (const [t, served, blocked] of saved.fine ?? []) this.fine.set(t, [served, blocked]);
      for (const [t, served, blocked] of saved.hour ?? []) this.hour.set(t, [served, blocked]);
      this.simTime = saved.simTime ?? 0;
      this.total = saved.total ?? 0;
    } catch {
      this.clear();
    }
  }
}

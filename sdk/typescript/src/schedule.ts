export const AIOS_SCHEDULE_META = '__aios_schedule__';

export interface ScheduleOptions {
  intervalMs: number;
  raw: string;
}

/**
 * Parse an interval string like "every 1h", "every 30m", "every 24h",
 * "every 2d", "every 45s" into milliseconds.
 */
export function parseInterval(interval: string): number {
  const normalized = interval.trim().toLowerCase();

  // Accept both "every 1h" and plain "1h"
  const stripped = normalized.startsWith('every ')
    ? normalized.slice(6).trim()
    : normalized;

  const match = stripped.match(/^(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)$/);
  if (!match) {
    throw new Error(
      `Invalid schedule interval: "${interval}". ` +
        `Expected format: "every <N><unit>" where unit is ms, s, m, h, or d. ` +
        `Examples: "every 30m", "every 1h", "every 24h".`
    );
  }

  const value = parseFloat(match[1]);
  const unit = match[2];

  const multipliers: Record<string, number> = {
    ms: 1,
    s: 1_000,
    m: 60_000,
    h: 3_600_000,
    d: 86_400_000,
  };

  return Math.round(value * multipliers[unit]);
}

/**
 * Mark a method as a scheduled task.
 *
 * Usage:
 *   @schedule('every 1h')
 *   async refresh(): Promise<void> { ... }
 *
 * When Agent.launch() detects a @schedule on the `run()` method (or any
 * method named in the subclass), it loops the agent at the given interval.
 * If placed on any other method, the agent base class calls that method
 * on the parsed interval via setInterval.
 */
export function schedule(interval: string): MethodDecorator {
  const intervalMs = parseInterval(interval);

  return function (
    _target: object,
    _propertyKey: string | symbol,
    descriptor: PropertyDescriptor
  ): PropertyDescriptor {
    const meta: ScheduleOptions = { intervalMs, raw: interval };
    (descriptor.value as Record<string | symbol, unknown>)[AIOS_SCHEDULE_META] =
      meta;
    return descriptor;
  };
}

/**
 * Retrieve schedule metadata from a method, if any.
 */
export function getSchedule(fn: Function): ScheduleOptions | undefined {
  return (fn as Record<string | symbol, unknown>)[
    AIOS_SCHEDULE_META
  ] as ScheduleOptions | undefined;
}

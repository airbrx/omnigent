/** Shared formatters for the admin surface (Users / Sessions / Hosts). */

/** A local date-time string from Unix epoch seconds. */
export function formatEpoch(epoch: number): string {
  return new Date(epoch * 1000).toLocaleString();
}

/**
 * Format a USD cost. Sub-cent spend still shows as `$0.00` rather than being
 * hidden, so a session with negligible-but-nonzero cost reads as "cheap".
 */
export function formatUsd(cost: number): string {
  return `$${cost.toFixed(2)}`;
}

/** Compact token count: 1234 → "1.2K", 1500000 → "1.5M". */
export function formatTokens(tokens: number): string {
  if (tokens < 1000) return String(tokens);
  if (tokens < 1_000_000) return `${(tokens / 1000).toFixed(1)}K`;
  return `${(tokens / 1_000_000).toFixed(1)}M`;
}

/** Host count with a live-subset hint: 0 → "—", 2 with 1 live → "2 · 1 online". */
export function formatHosts(total: number, online: number): string {
  if (total === 0) return "—";
  if (online > 0) return `${total} · ${online} online`;
  return String(total);
}

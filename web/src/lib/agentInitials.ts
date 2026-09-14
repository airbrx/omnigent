// Deterministic initials + colour for an agent with no uploaded avatar, so
// the drawer reads as a roster of distinct teammates on day one rather than
// a column of identical grey circles.

const PALETTE = [
  "bg-sky-600",
  "bg-emerald-600",
  "bg-violet-600",
  "bg-amber-600",
  "bg-rose-600",
  "bg-teal-600",
  "bg-indigo-600",
  "bg-orange-600",
] as const;

/** Up to two uppercase letters for an agent name; "?" when there are none. */
export function agentInitials(name: string): string {
  const words = name.split(/[\s\-_]+/u).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

/** Stable Tailwind background class for a name. Same name, same colour. */
export function agentAvatarColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

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
  // Array.from splits on code points rather than UTF-16 code units, so an
  // astral-plane emoji (e.g. this fork's "🐄 Cache Cow" persona names) is
  // taken whole instead of being cut mid-surrogate-pair into a mangled
  // glyph. Both branches index the same way for the same reason.
  if (words.length === 1) return Array.from(words[0]).slice(0, 2).join("").toUpperCase();
  const firstCodePoint = (word: string) => Array.from(word)[0] ?? "";
  return (firstCodePoint(words[0]) + firstCodePoint(words[1])).toUpperCase();
}

/** Stable Tailwind background class for a name. Same name, same colour. */
export function agentAvatarColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

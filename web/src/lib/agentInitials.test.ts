import { describe, expect, it } from "vitest";

import { agentAvatarColor, agentInitials } from "./agentInitials";

describe("agentInitials", () => {
  it("takes the first letter of the first two words", () => {
    expect(agentInitials("cache cow")).toBe("CC");
  });

  it("takes the first two letters of a single word", () => {
    expect(agentInitials("researcher")).toBe("RE");
  });

  it("handles separators and extra whitespace", () => {
    expect(agentInitials("  cache-hound  ")).toBe("CH");
    expect(agentInitials("cache_register")).toBe("CR");
  });

  it("falls back to ? on an empty name", () => {
    expect(agentInitials("")).toBe("?");
    expect(agentInitials("   ")).toBe("?");
  });

  it("keeps an astral-plane emoji whole in a single-word name", () => {
    // "🐮" is a surrogate pair (2 UTF-16 code units); slicing code units
    // instead of code points would cut it in half and render a mangled
    // glyph plus a stray trailing letter.
    expect(agentInitials("🐮Cow")).toBe("🐮C");
  });

  it("keeps an emoji-led first word whole in a multi-word name", () => {
    // This fork's agent personas are emoji-led ("🐄 Cache Cow"), so indexing
    // words[0][0] would grab a lone high surrogate instead of the emoji.
    expect(agentInitials("🐄 Cache Cow")).toBe("🐄C");
  });
});

describe("agentAvatarColor", () => {
  it("is deterministic for the same name", () => {
    expect(agentAvatarColor("researcher")).toBe(agentAvatarColor("researcher"));
  });

  it("distinguishes different names", () => {
    expect(agentAvatarColor("alpha")).not.toBe(agentAvatarColor("zulu"));
  });
});

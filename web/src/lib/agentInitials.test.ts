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
});

describe("agentAvatarColor", () => {
  it("is deterministic for the same name", () => {
    expect(agentAvatarColor("researcher")).toBe(agentAvatarColor("researcher"));
  });

  it("distinguishes different names", () => {
    expect(agentAvatarColor("alpha")).not.toBe(agentAvatarColor("zulu"));
  });
});

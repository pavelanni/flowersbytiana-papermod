import { describe, it, expect, vi } from "vitest";
import { verifyTurnstile } from "./turnstile.js";

describe("verifyTurnstile", () => {
  it("returns true when Cloudflare reports success", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      json: async () => ({ success: true }),
    });

    const result = await verifyTurnstile("token123", "secret123", "1.2.3.4", fetchImpl);

    expect(result).toBe(true);
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://challenges.cloudflare.com/turnstile/v0/siteverify",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret: "secret123", response: "token123", remoteip: "1.2.3.4" }),
      })
    );
  });

  it("returns false when Cloudflare reports failure", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      json: async () => ({ success: false, "error-codes": ["invalid-input-response"] }),
    });

    const result = await verifyTurnstile("bad-token", "secret123", "1.2.3.4", fetchImpl);

    expect(result).toBe(false);
  });
});

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { handleContact } from "./contact.js";

// These tests deliberately do NOT mock ./turnstile.js or ./resend.js — they
// stub the network layer (global fetch) instead, so they exercise the real
// verifyTurnstile/sendContactEmail code paths together with handleContact.
// This catches integration bugs (e.g. a renamed parameter) that pure
// module-mock tests in contact.test.js cannot.

function makeEnv() {
  return {
    TURNSTILE_SECRET_KEY: "turnstile-secret",
    RESEND_API_KEY: "re_test_key",
    CONTACT_TO_EMAILS: "pavel.anni@gmail.com,tatiana.batik@gmail.com",
    CONTACT_FROM_EMAIL: "Flowers by Tiana <contact@flowersbytiana.com>",
  };
}

function makeFormRequest(fields) {
  const formData = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    formData.set(key, value);
  }
  return new Request("https://flowersbytiana.com/api/contact", {
    method: "POST",
    body: formData,
    headers: { "CF-Connecting-IP": "1.2.3.4" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("handleContact (real turnstile.js/resend.js, stubbed fetch)", () => {
  it("returns a 400 JSON error instead of throwing when the request body isn't form data", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const request = new Request("https://flowersbytiana.com/api/contact", {
      method: "POST",
      body: "not form data",
      headers: { "Content-Type": "application/json" },
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(400);
    expect(body.ok).toBe(false);
    expect(typeof body.error).toBe("string");
    expect(body.error.length).toBeGreaterThan(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns a 400 JSON error instead of throwing when the Turnstile siteverify request fails", async () => {
    const fetchMock = vi.fn(async (url) => {
      if (url.toString().includes("challenges.cloudflare.com")) {
        throw new Error("network outage");
      }
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const request = makeFormRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000",
      "cf-turnstile-response": "some-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(400);
    expect(body.ok).toBe(false);
    expect(typeof body.error).toBe("string");
    expect(body.error.length).toBeGreaterThan(0);
  });

  it("strips embedded CRLF from the piece field before it reaches the outgoing email", async () => {
    let resendRequestBody;
    const fetchMock = vi.fn(async (url, options) => {
      const target = url.toString();
      if (target.includes("challenges.cloudflare.com")) {
        return { json: async () => ({ success: true }) };
      }
      if (target.includes("api.resend.com")) {
        resendRequestBody = JSON.parse(options.body);
        return { ok: true, json: async () => ({ id: "email_123" }) };
      }
      throw new Error(`unexpected fetch to ${target}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const maliciousPiece = "orchids-000\r\nBcc: evil@example.com";
    const request = makeFormRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: maliciousPiece,
      "cf-turnstile-response": "good-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(resendRequestBody).toBeDefined();
    // The subject line must never contain a raw CR/LF (header-injection vector).
    expect(resendRequestBody.subject).not.toMatch(/[\r\n]/);
    expect(resendRequestBody.subject).toBe("New inquiry: orchids-000 Bcc: evil@example.com");
    // The body text is expected to contain structural newlines (From:/Piece:/message),
    // but the "Piece:" line itself must be a single sanitized line with no injected CRLF.
    const pieceLine = resendRequestBody.text
      .split("\n")
      .find((line) => line.startsWith("Piece:"));
    expect(pieceLine).toBe("Piece: orchids-000 Bcc: evil@example.com");
  });
});

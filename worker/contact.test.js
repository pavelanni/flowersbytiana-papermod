import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("./turnstile.js", () => ({
  verifyTurnstile: vi.fn(),
}));
vi.mock("./resend.js", () => ({
  sendContactEmail: vi.fn(),
}));

import { verifyTurnstile } from "./turnstile.js";
import { sendContactEmail } from "./resend.js";
import { handleContact } from "./contact.js";

function makeEnv() {
  return {
    TURNSTILE_SECRET_KEY: "turnstile-secret",
    RESEND_API_KEY: "re_test_key",
    CONTACT_TO_EMAILS: "pavel.anni@gmail.com,tatiana.batik@gmail.com",
    CONTACT_FROM_EMAIL: "Flowers by Tiana <contact@flowersbytiana.com>",
  };
}

function makeRequest(fields) {
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

describe("handleContact", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("rejects a submission missing required fields", async () => {
    const request = makeRequest({ name: "", email: "not-an-email", message: "" });

    const response = await handleContact(request, makeEnv());

    expect(response.status).toBe(400);
    expect(verifyTurnstile).not.toHaveBeenCalled();
  });

  it("rejects when Turnstile verification fails", async () => {
    verifyTurnstile.mockResolvedValue(false);
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000",
      "cf-turnstile-response": "bad-token",
    });

    const response = await handleContact(request, makeEnv());

    expect(response.status).toBe(400);
    expect(sendContactEmail).not.toHaveBeenCalled();
  });

  it("sends the email and returns ok on a valid submission", async () => {
    verifyTurnstile.mockResolvedValue(true);
    sendContactEmail.mockResolvedValue({ id: "email_123" });
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000",
      "cf-turnstile-response": "good-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body).toEqual({ ok: true });
    expect(sendContactEmail).toHaveBeenCalledWith({
      apiKey: "re_test_key",
      from: "Flowers by Tiana <contact@flowersbytiana.com>",
      to: ["pavel.anni@gmail.com", "tatiana.batik@gmail.com"],
      replyTo: "jane@example.com",
      subject: "New inquiry: orchids-000",
      text: "From: Jane <jane@example.com>\nPiece: orchids-000\n\nIs this available?",
    });
  });

  it("uses a general subject line when no piece is given", async () => {
    verifyTurnstile.mockResolvedValue(true);
    sendContactEmail.mockResolvedValue({ id: "email_456" });
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Do you have anything similar to the lotus paintings?",
      "cf-turnstile-response": "good-token",
    });

    await handleContact(request, makeEnv());

    expect(sendContactEmail).toHaveBeenCalledWith(
      expect.objectContaining({ subject: "New inquiry from flowersbytiana.com" })
    );
  });

  it("returns a 502 when sendContactEmail throws (server-side dependency failure)", async () => {
    verifyTurnstile.mockResolvedValue(true);
    sendContactEmail.mockRejectedValue(new Error("Resend API error 500: Service unavailable"));
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000",
      "cf-turnstile-response": "good-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(502);
    expect(body.ok).toBe(false);
    expect(body.error).toBeTruthy();
    expect(typeof body.error).toBe("string");
  });

  it("returns a 400 JSON error instead of throwing when the request body isn't form data", async () => {
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
    expect(verifyTurnstile).not.toHaveBeenCalled();
  });

  it("returns a 400 JSON error instead of throwing when verifyTurnstile rejects (network failure)", async () => {
    verifyTurnstile.mockRejectedValue(new Error("fetch failed"));
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000",
      "cf-turnstile-response": "good-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(400);
    expect(body.ok).toBe(false);
    expect(typeof body.error).toBe("string");
    expect(body.error.length).toBeGreaterThan(0);
    expect(sendContactEmail).not.toHaveBeenCalled();
  });

  it("sanitizes a piece value containing embedded CRLF before it reaches the email", async () => {
    verifyTurnstile.mockResolvedValue(true);
    sendContactEmail.mockResolvedValue({ id: "email_789" });
    const request = makeRequest({
      name: "Jane",
      email: "jane@example.com",
      message: "Is this available?",
      piece: "orchids-000\r\nBcc: evil@example.com",
      "cf-turnstile-response": "good-token",
    });

    const response = await handleContact(request, makeEnv());
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(sendContactEmail).toHaveBeenCalledTimes(1);
    const call = sendContactEmail.mock.calls[0][0];
    expect(call.subject).not.toMatch(/[\r\n]/);
    expect(call.text).not.toMatch(/Piece:.*[\r\n].*Bcc:/);
  });
});

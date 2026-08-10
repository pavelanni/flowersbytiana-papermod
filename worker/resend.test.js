import { describe, it, expect, vi } from "vitest";
import { sendContactEmail } from "./resend.js";

describe("sendContactEmail", () => {
  it("posts the expected payload to Resend", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "email_123" }),
    });

    const result = await sendContactEmail(
      {
        apiKey: "re_test_key",
        from: "Flowers by Tiana <contact@flowersbytiana.com>",
        to: ["pavel.anni@gmail.com", "tatiana.batik@gmail.com"],
        replyTo: "visitor@example.com",
        subject: "New inquiry: orchids-000",
        text: "From: Jane <visitor@example.com>\nPiece: orchids-000\n\nIs this still available?",
      },
      fetchImpl
    );

    expect(result).toEqual({ id: "email_123" });
    expect(fetchImpl).toHaveBeenCalledWith(
      "https://api.resend.com/emails",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer re_test_key",
          "Content-Type": "application/json",
        },
      })
    );
    const [, options] = fetchImpl.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      from: "Flowers by Tiana <contact@flowersbytiana.com>",
      to: ["pavel.anni@gmail.com", "tatiana.batik@gmail.com"],
      reply_to: "visitor@example.com",
      subject: "New inquiry: orchids-000",
      text: "From: Jane <visitor@example.com>\nPiece: orchids-000\n\nIs this still available?",
    });
  });

  it("throws when Resend responds with an error status", async () => {
    const fetchImpl = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      text: async () => "invalid from address",
    });

    await expect(
      sendContactEmail(
        {
          apiKey: "re_test_key",
          from: "bad",
          to: ["pavel.anni@gmail.com"],
          replyTo: "visitor@example.com",
          subject: "subj",
          text: "body",
        },
        fetchImpl
      )
    ).rejects.toThrow("Resend API error 422: invalid from address");
  });
});

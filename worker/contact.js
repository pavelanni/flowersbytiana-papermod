import { verifyTurnstile } from "./turnstile.js";
import { sendContactEmail } from "./resend.js";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function handleContact(request, env) {
  try {
    let formData;
    try {
      formData = await request.formData();
    } catch (error) {
      console.error("Contact form: failed to parse request body as form data", error);
      throw error;
    }
    const name = (formData.get("name") || "").toString().trim();
    const email = (formData.get("email") || "").toString().trim();
    const message = (formData.get("message") || "").toString().trim();
    const piece = (formData.get("piece") || "").toString().trim();
    const turnstileToken = (formData.get("cf-turnstile-response") || "").toString();

    if (!name || !message || !EMAIL_PATTERN.test(email)) {
      return Response.json(
        { ok: false, error: "Please fill in your name, a valid email, and a message." },
        { status: 400 }
      );
    }

    let verified;
    try {
      verified = await verifyTurnstile(
        turnstileToken,
        env.TURNSTILE_SECRET_KEY,
        request.headers.get("CF-Connecting-IP")
      );
    } catch (error) {
      console.error("Contact form: Turnstile verification request failed", error);
      throw error;
    }
    if (!verified) {
      return Response.json(
        { ok: false, error: "Spam check failed, please reload the page and try again." },
        { status: 400 }
      );
    }

    const safePiece = piece.replace(/[\r\n]+/g, " ").slice(0, 200);
    const toAddresses = (env.CONTACT_TO_EMAILS || "")
      .split(",")
      .map((address) => address.trim())
      .filter(Boolean);
    const subject = safePiece ? `New inquiry: ${safePiece}` : "New inquiry from flowersbytiana.com";
    const text = `From: ${name} <${email}>\nPiece: ${safePiece || "(not specified)"}\n\n${message}`;

    try {
      await sendContactEmail({
        apiKey: env.RESEND_API_KEY,
        from: env.CONTACT_FROM_EMAIL,
        to: toAddresses,
        replyTo: email,
        subject,
        text,
      });
    } catch (error) {
      console.error("Contact form: sendContactEmail failed", error);
      return Response.json(
        { ok: false, error: "Something went wrong sending your message. Please try again." },
        { status: 502 }
      );
    }

    return Response.json({ ok: true });
  } catch (error) {
    console.error("Contact form: unexpected error in handleContact", error);
    return Response.json(
      { ok: false, error: "Something went wrong. Please try again." },
      { status: 400 }
    );
  }
}

export async function verifyTurnstile(token, secret, remoteip, fetchImpl = fetch) {
  const response = await fetchImpl(
    "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ secret, response: token, remoteip }),
    }
  );
  const data = await response.json();
  return data.success === true;
}

# Contact Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a visitor ask about buying a specific painting via a "Contact us about this piece" link that lands on a `/contact/` form; submitting it emails Pavel and Tiana, without turning the site into a dynamic app.

**Architecture:** Migrate deployment from Cloudflare Pages to a Cloudflare Worker with static assets. The Worker serves the existing Hugo `public/` build for every request except `POST /api/contact`, which it handles itself: verify a Cloudflare Turnstile token, then send an email via the Resend API. Two Hugo-side changes support this: the `img` shortcode grows an optional "size" argument that, when present, prints a size caption and a link to the pre-filled contact page; and a new `/contact/` page renders the form.

**Tech Stack:** Hugo (existing), Cloudflare Workers + Wrangler 4.120.0, plain JavaScript (no TypeScript, no framework), Vitest 4.1.10 for unit tests, Resend (email API), Cloudflare Turnstile (spam protection).

## Global Constraints

- Worker code is plain JavaScript (`.js`), not TypeScript — this project has no TS toolchain and doesn't need one for this scope.
- No `@cloudflare/vitest-pool-workers` — the Worker logic only uses standard Fetch API primitives (`Request`, `Response`, `FormData`, `fetch`) plus one trivially-mockable `env.ASSETS.fetch` binding, all of which Node 22's built-in `undici`-based fetch already provides. Plain Vitest in its default Node environment is sufficient (verified: `Response.json`, `new FormData()` both work in this repo's Node v22.23.1).
- Secrets (`RESEND_API_KEY`, `TURNSTILE_SECRET_KEY`) are set via `wrangler secret put` and read from `env`; never written to any committed file.
- The 173 existing `{{< img "path" "alt" >}}` calls (no 3rd argument) must render byte-for-byte identical to today — the size argument and the "Contact us about this piece" link are additive and only appear when a 3rd argument is supplied.
- Contact-form recipients: `pavel.anni@gmail.com` and `tatiana.batik@gmail.com`. Sending address: `Flowers by Tiana <contact@flowersbytiana.com>`.
- Resend's raw REST API field for reply-to is `reply_to` (snake_case) — confirmed against `resend.com/docs/api-reference/emails/send-email`, not the `replyTo` name used by Resend's Node SDK (which this project doesn't use).
- Turnstile server-side verification endpoint: `POST https://challenges.cloudflare.com/turnstile/v0/siteverify` with JSON body `{ secret, response, remoteip }` — confirmed against `developers.cloudflare.com/turnstile/get-started/server-side-validation/`.

---

### Task 1: Turnstile verification module

**Files:**
- Create: `package.json`
- Create: `vitest.config.js`
- Create: `worker/turnstile.js`
- Test: `worker/turnstile.test.js`
- Modify: `.gitignore` (add `node_modules` and `.dev.vars`)

**Interfaces:**
- Produces: `verifyTurnstile(token: string, secret: string, remoteip: string | null, fetchImpl = fetch): Promise<boolean>` — later tasks (Task 3) import this from `./turnstile.js`.

- [ ] **Step 1: Create `package.json`**

```json
{
  "name": "flowersbytiana-worker",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  },
  "devDependencies": {
    "vitest": "^4.1.10",
    "wrangler": "^4.120.0"
  }
}
```

- [ ] **Step 2: Create `vitest.config.js`**

```js
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["worker/**/*.test.js"],
  },
});
```

- [ ] **Step 3: Add `node_modules` and `.dev.vars` to `.gitignore`**

Append these two lines to the existing `.gitignore`:

```
node_modules
.dev.vars
```

- [ ] **Step 4: Install dependencies**

Run: `npm install`
Expected: completes without error, creates `node_modules/` and `package-lock.json`.

- [ ] **Step 5: Write the failing test**

Create `worker/turnstile.test.js`:

```js
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `npx vitest run worker/turnstile.test.js`
Expected: FAIL — `worker/turnstile.js` does not exist yet.

- [ ] **Step 7: Write minimal implementation**

Create `worker/turnstile.js`:

```js
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
```

- [ ] **Step 8: Run test to verify it passes**

Run: `npx vitest run worker/turnstile.test.js`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add package.json package-lock.json vitest.config.js worker/turnstile.js worker/turnstile.test.js .gitignore
git commit -m "Add Turnstile verification module"
```

---

### Task 2: Resend email module

**Files:**
- Create: `worker/resend.js`
- Test: `worker/resend.test.js`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `sendContactEmail({ apiKey, from, to, replyTo, subject, text }, fetchImpl = fetch): Promise<{id: string}>` — throws on a non-OK HTTP response. Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Create `worker/resend.test.js`:

```js
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run worker/resend.test.js`
Expected: FAIL — `worker/resend.js` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `worker/resend.js`:

```js
export async function sendContactEmail(
  { apiKey, from, to, replyTo, subject, text },
  fetchImpl = fetch
) {
  const response = await fetchImpl("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from,
      to,
      reply_to: replyTo,
      subject,
      text,
    }),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Resend API error ${response.status}: ${body}`);
  }

  return response.json();
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run worker/resend.test.js`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/resend.js worker/resend.test.js
git commit -m "Add Resend email-sending module"
```

---

### Task 3: Contact form handler

**Files:**
- Create: `worker/contact.js`
- Test: `worker/contact.test.js`

**Interfaces:**
- Consumes: `verifyTurnstile` from `./turnstile.js` (Task 1); `sendContactEmail` from `./resend.js` (Task 2).
- Produces: `handleContact(request: Request, env): Promise<Response>` where `env` has `TURNSTILE_SECRET_KEY`, `RESEND_API_KEY`, `CONTACT_TO_EMAILS` (comma-separated string), `CONTACT_FROM_EMAIL` (string). Consumed by Task 4. Response body on success: `{ ok: true }` (200); on failure: `{ ok: false, error: string }` (400).

- [ ] **Step 1: Write the failing test**

Create `worker/contact.test.js`:

```js
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run worker/contact.test.js`
Expected: FAIL — `worker/contact.js` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `worker/contact.js`:

```js
import { verifyTurnstile } from "./turnstile.js";
import { sendContactEmail } from "./resend.js";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function handleContact(request, env) {
  const formData = await request.formData();
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

  const verified = await verifyTurnstile(
    turnstileToken,
    env.TURNSTILE_SECRET_KEY,
    request.headers.get("CF-Connecting-IP")
  );
  if (!verified) {
    return Response.json(
      { ok: false, error: "Spam check failed, please reload the page and try again." },
      { status: 400 }
    );
  }

  const toAddresses = env.CONTACT_TO_EMAILS.split(",").map((address) => address.trim());
  const subject = piece ? `New inquiry: ${piece}` : "New inquiry from flowersbytiana.com";
  const text = `From: ${name} <${email}>\nPiece: ${piece || "(not specified)"}\n\n${message}`;

  await sendContactEmail({
    apiKey: env.RESEND_API_KEY,
    from: env.CONTACT_FROM_EMAIL,
    to: toAddresses,
    replyTo: email,
    subject,
    text,
  });

  return Response.json({ ok: true });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run worker/contact.test.js`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add worker/contact.js worker/contact.test.js
git commit -m "Add contact form submission handler"
```

---

### Task 4: Worker entry point and router

**Files:**
- Create: `worker/index.js`
- Test: `worker/index.test.js`
- Create: `wrangler.jsonc`

**Interfaces:**
- Consumes: `handleContact` from `./contact.js` (Task 3).
- Produces: default-exported Worker object with `fetch(request, env)`, the entry named by `wrangler.jsonc`'s `main`.

- [ ] **Step 1: Write the failing test**

Create `worker/index.test.js`:

```js
import { describe, it, expect, vi } from "vitest";

vi.mock("./contact.js", () => ({
  handleContact: vi.fn(),
}));

import { handleContact } from "./contact.js";
import worker from "./index.js";

describe("worker fetch router", () => {
  it("routes POST /api/contact to handleContact", async () => {
    handleContact.mockResolvedValue(new Response("ok"));
    const request = new Request("https://flowersbytiana.com/api/contact", { method: "POST" });
    const env = { ASSETS: { fetch: vi.fn() } };

    await worker.fetch(request, env);

    expect(handleContact).toHaveBeenCalledWith(request, env);
    expect(env.ASSETS.fetch).not.toHaveBeenCalled();
  });

  it("falls through to static assets for any other request", async () => {
    const assetsResponse = new Response("<html>page</html>");
    const env = { ASSETS: { fetch: vi.fn().mockResolvedValue(assetsResponse) } };
    const request = new Request("https://flowersbytiana.com/albums/orchids/");

    const response = await worker.fetch(request, env);

    expect(env.ASSETS.fetch).toHaveBeenCalledWith(request);
    expect(response).toBe(assetsResponse);
  });

  it("falls through when /api/contact is requested with a non-POST method", async () => {
    const assetsResponse = new Response("not found", { status: 404 });
    const env = { ASSETS: { fetch: vi.fn().mockResolvedValue(assetsResponse) } };
    const request = new Request("https://flowersbytiana.com/api/contact", { method: "GET" });

    const response = await worker.fetch(request, env);

    expect(handleContact).not.toHaveBeenCalled();
    expect(env.ASSETS.fetch).toHaveBeenCalledWith(request);
    expect(response).toBe(assetsResponse);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run worker/index.test.js`
Expected: FAIL — `worker/index.js` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `worker/index.js`:

```js
import { handleContact } from "./contact.js";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/contact" && request.method === "POST") {
      return handleContact(request, env);
    }
    return env.ASSETS.fetch(request);
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run worker/index.test.js`
Expected: PASS (3 tests)

- [ ] **Step 5: Create `wrangler.jsonc`**

```jsonc
{
  "name": "flowersbytiana",
  "main": "worker/index.js",
  "compatibility_date": "2026-08-09",
  "assets": {
    "directory": "./public",
    "binding": "ASSETS",
    "run_worker_first": ["/api/*"]
  },
  "vars": {
    "CONTACT_TO_EMAILS": "pavel.anni@gmail.com,tatiana.batik@gmail.com",
    "CONTACT_FROM_EMAIL": "Flowers by Tiana <contact@flowersbytiana.com>"
  }
}
```

Note: `assets.directory` points at Hugo's build output, so `hugo` must be run to (re)generate `public/` before `wrangler dev` or `wrangler deploy`.

- [ ] **Step 6: Run the full test suite**

Run: `npx vitest run`
Expected: PASS (all tests across turnstile, resend, contact, index — 11 tests total)

- [ ] **Step 7: Commit**

```bash
git add worker/index.js worker/index.test.js wrangler.jsonc
git commit -m "Add Worker entry point routing /api/contact vs static assets"
```

---

### Task 5: Extend the `img` shortcode with size + "contact us" link

**Files:**
- Modify: `layouts/shortcodes/img.html`

**Interfaces:**
- Consumes: nothing from other tasks (pure Hugo template change).
- Produces: when a 3rd shortcode argument is given, renders `<p class="caption">SIZE</p>` followed by a link to `/contact/?piece=<slug>&title=<alt>`, where `<slug>` is the image filename without its directory or extension (e.g. `orchids/orchids-000.jpg` → `orchids-000`).

- [ ] **Step 1: Read the current shortcode**

Current content of `layouts/shortcodes/img.html`:

```html
{{ $imageCdn := .Site.Params.imageCdn }}
{{ $imageName := .Get 0 }}
{{ $alt := .Get 1 }}
{{ $caption := .Get 2 }}

<figure>
  <img src="{{ $imageCdn }}{{ $imageName }}" alt="{{ $alt }}" />
  {{ with $caption }}<p class="caption">{{ . }}</p>{{ end }}
</figure>
```

- [ ] **Step 2: Replace it with the extended version**

```html
{{ $imageCdn := .Site.Params.imageCdn }}
{{ $imageName := .Get 0 }}
{{ $alt := .Get 1 }}
{{ $caption := .Get 2 }}

<figure>
  <img src="{{ $imageCdn }}{{ $imageName }}" alt="{{ $alt }}" />
  {{ with $caption }}
  <p class="caption">{{ . }}</p>
  {{ $pieceID := path.Base $imageName }}
  {{ $pieceID = $pieceID | strings.TrimSuffix (path.Ext $pieceID) }}
  <p class="contact-link"><a href="/contact/?piece={{ $pieceID | urlquery }}&amp;title={{ $alt | urlquery }}">Contact us about this piece</a></p>
  {{ end }}
</figure>
```

- [ ] **Step 3: Verify existing images are unaffected**

Run: `hugo --quiet && grep -c "Contact us about this piece" public/albums/narcissus/index.html`
Expected: `0` — the narcissus album has no photo with a 3rd shortcode argument, so no button should appear.

- [ ] **Step 4: Verify the new behavior with a temporary fixture**

Create a scratch content page (outside `content/albums`, so it can't be mistaken for real gallery content) at `content/_shortcode-check/index.md`:

```markdown
---
title: 'Shortcode Check'
draft: false
---

{{< img "orchids/orchids-000.jpg" "Orchid 000" "16 x 20 in" >}}
```

Run: `hugo --quiet`

Then check the rendered output:

Run: `grep -A2 'class="caption"' public/_shortcode-check/index.html`
Expected output includes:
```
<p class="caption">16 x 20 in</p>
<p class="contact-link"><a href="/contact/?piece=orchids-000&amp;title=Orchid+000">Contact us about this piece</a></p>
```

- [ ] **Step 5: Delete the scratch fixture**

```bash
rm -rf content/_shortcode-check
hugo --quiet
```

Run: `git status --short content/` to confirm nothing under `content/_shortcode-check` remains and no real album content changed.

- [ ] **Step 6: Commit**

```bash
git add layouts/shortcodes/img.html
git commit -m "Extend img shortcode with size caption and per-piece contact link"
```

---

### Task 6: The `/contact/` page

**Files:**
- Create: `content/contact/index.md`
- Create: `layouts/contact/single.html`
- Modify: `hugo.yaml` (add `turnstileSiteKey` param)

**Interfaces:**
- Consumes: reads `piece` and `title` query parameters set by Task 5's links; posts to `POST /api/contact` (Task 3/4's contract: form fields `name`, `email`, `message`, `piece`, `cf-turnstile-response`; JSON response `{ ok: boolean, error?: string }`).
- Produces: nothing consumed by other tasks — this is the last piece connecting the two sides.

- [ ] **Step 1: Add the Turnstile site key placeholder to `hugo.yaml`**

In `hugo.yaml`, under the existing `params:` block (after `imageCdn`), add:

```yaml
  # Site key from the Cloudflare Turnstile widget for flowersbytiana.com.
  # Filled in during manual setup (Task 7) — safe to be public, unlike the secret key.
  turnstileSiteKey: ""
```

- [ ] **Step 2: Create `content/contact/index.md`**

```markdown
---
title: 'Contact Us'
hiddenInHomeList: true
ShowToc: false
---
```

- [ ] **Step 3: Create `layouts/contact/single.html`**

```html
{{ define "main" }}
<article class="post-single">
  <header class="post-header">
    <h1 class="post-title">Contact Us</h1>
    <div class="post-description">
      <p>Interested in one of Tiana's paintings? Send us a message and we'll get back to you.</p>
    </div>
  </header>

  <form id="contact-form" class="contact-form">
    <p id="piece-wrap">
      <label id="piece-label" for="piece-input">Which piece are you asking about? (optional)</label><br>
      <input type="text" id="piece-input" name="piece" placeholder="e.g. Orchid 000, or leave blank for a general question">
    </p>
    <p>
      <label for="contact-name">Name</label><br>
      <input type="text" id="contact-name" name="name" required>
    </p>
    <p>
      <label for="contact-email">Email</label><br>
      <input type="email" id="contact-email" name="email" required>
    </p>
    <p>
      <label for="contact-message">Message</label><br>
      <textarea id="contact-message" name="message" rows="6" required></textarea>
    </p>
    <div class="cf-turnstile" data-sitekey="{{ site.Params.turnstileSiteKey }}"></div>
    <p><button type="submit">Send</button></p>
    <p id="contact-status" role="status"></p>
  </form>

  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
  <script>
    (function () {
      const params = new URLSearchParams(window.location.search);
      const piece = params.get("piece");
      const title = params.get("title");

      if (piece) {
        const input = document.getElementById("piece-input");
        const label = document.getElementById("piece-label");
        input.value = title ? `${title} (${piece})` : piece;
        input.readOnly = true;
        label.textContent = "Regarding";
      }

      const form = document.getElementById("contact-form");
      const status = document.getElementById("contact-status");

      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        status.textContent = "Sending...";
        try {
          const response = await fetch("/api/contact", {
            method: "POST",
            body: new FormData(form),
          });
          const data = await response.json();
          if (data.ok) {
            status.textContent = "Thanks! We'll be in touch soon.";
            form.reset();
          } else {
            status.textContent = data.error || "Something went wrong. Please try again.";
          }
        } catch (err) {
          status.textContent = "Something went wrong. Please try again.";
        }
      });
    })();
  </script>

  <style>
    .contact-form label { font-weight: 600; }
    .contact-form input[type="text"],
    .contact-form input[type="email"],
    .contact-form textarea {
      width: 100%;
      max-width: 32rem;
      padding: 0.5rem;
      margin-top: 0.25rem;
      box-sizing: border-box;
    }
    .contact-form button {
      padding: 0.5rem 1.5rem;
    }
  </style>
</article>
{{ end }}
```

- [ ] **Step 4: Build and verify the page renders**

Run: `hugo --quiet && grep -o 'Contact Us' public/contact/index.html | head -1`
Expected: `Contact Us`

Run: `grep -c 'name="message"' public/contact/index.html`
Expected: `1`

Run: `grep -c 'challenges.cloudflare.com/turnstile' public/contact/index.html`
Expected: `1`

- [ ] **Step 5: Verify the page is excluded from the home/albums listing**

Run: `grep -c 'Contact Us' public/index.html`
Expected: `0` (the `hiddenInHomeList: true` front matter keeps it out of the home page list)

- [ ] **Step 6: Commit**

```bash
git add content/contact/index.md layouts/contact/single.html hugo.yaml
git commit -m "Add /contact/ page with pre-filled piece field and Turnstile widget"
```

---

### Task 7: Manual account setup and production cutover (human-only — not delegable to a subagent)

This task needs a real Cloudflare dashboard, a real Resend account, and DNS changes to a live production domain. No subagent can complete it — it requires a human with account access. Do this yourself (Pavel), following the steps below in order.

**Steps:**

- [ ] **Step 1: Create and verify a Resend sending domain**
  1. Sign up at resend.com (free tier: 3,000 emails/month).
  2. Add `flowersbytiana.com` as a sending domain.
  3. Add the DNS records Resend shows you (SPF/DKIM TXT records) to the domain's DNS zone in the Cloudflare dashboard — the same zone that already hosts the `cdn.flowersbytiana.com` CNAME for R2.
  4. Wait for Resend to show the domain as verified (usually a few minutes, can take longer for DNS propagation).
  5. Create an API key in Resend, scoped to sending only if that option is offered.

- [ ] **Step 2: Create a Turnstile widget**
  1. In the Cloudflare dashboard, go to Turnstile and create a new widget for `flowersbytiana.com`.
  2. Choose the "Managed" challenge type (invisible unless Cloudflare suspects the visitor is a bot).
  3. Note the Site Key (public) and Secret Key (private).

- [ ] **Step 3: Fill in the Turnstile site key**

Edit `hugo.yaml`, replacing the `turnstileSiteKey: ""` line added in Task 6 with the real site key:

```yaml
  turnstileSiteKey: "PASTE_YOUR_SITE_KEY_HERE"
```

```bash
git add hugo.yaml
git commit -m "Add production Turnstile site key"
```

- [ ] **Step 4: Set the Worker secrets**

```bash
npx wrangler secret put RESEND_API_KEY
# paste the Resend API key from Step 1 when prompted

npx wrangler secret put TURNSTILE_SECRET_KEY
# paste the Turnstile secret key from Step 2 when prompted
```

- [ ] **Step 5: Deploy to a preview URL and smoke-test before touching the live domain**

```bash
hugo --quiet
npx wrangler deploy
```

Wrangler prints a `*.workers.dev` URL. Open it in a browser:
1. Click through to an album, confirm photos and navigation still work exactly as on the current live site.
2. Add a size argument to one real photo's `{{< img >}}` call temporarily (pick a piece Tiana has actually agreed to list, with its real size), rebuild, redeploy, and click its new "Contact us about this piece" link.
3. Confirm the `/contact/` page shows "Regarding: <that piece>", fill in the form, and submit.
4. Confirm the Turnstile widget completes without visible friction (managed mode should be invisible for a normal browser).
5. Confirm an email arrives at **both** `pavel.anni@gmail.com` and `tatiana.batik@gmail.com`, with the visitor's email set as Reply-To.

If anything fails, fix it and redeploy to the same preview URL before proceeding — do not touch the production domain until this smoke test passes cleanly.

- [ ] **Step 6: Cut the production domain over from Pages to the Worker**

This is the one step that affects the live site — do it deliberately, not as part of an automated run:
1. In the Cloudflare dashboard, add `flowersbytiana.com` as a custom domain on the new Worker (Workers & Pages → your Worker → Settings → Domains & Routes).
2. Once Cloudflare confirms the Worker is serving the custom domain, remove the custom domain binding from the old Pages project (or leave the Pages project in place, unbound, as a fallback for a few days before deleting it).
3. Visit `https://flowersbytiana.com/` directly and re-run the same smoke test from Step 5 against the live domain.

- [ ] **Step 7: Update the deploy workflow note**

If there's a personal note, README, or muscle-memory command for "how I deploy this site," update it to `hugo && npx wrangler deploy` instead of whatever the old Pages deploy step was.

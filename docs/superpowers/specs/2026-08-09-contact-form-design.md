# Contact Form Design

Date: 2026-08-09

## Goal

Let a visitor ask about buying a specific painting without turning the site
into a dynamic app. A "Contact us about this piece" button under a for-sale
photo leads to a small form; submitting it emails Pavel/Tiana with the
visitor's message, their reply-to email, and which piece they mean.

This is step one of monetizing Tiana's paintings (sold previously via Etsy,
which raised its fees). Step two — direct checkout via Stripe Payment Links —
is a deliberately separate follow-up spec once this ships.

## Why Workers instead of Pages

The site currently deploys via Cloudflare Pages. Cloudflare's current guidance
(confirmed against `developers.cloudflare.com` docs, August 2026) is that new
projects should start on **Workers with static assets**, not Pages: Workers
now serves static files natively (via an `[assets]` block in
`wrangler.jsonc`) in the same deployment as server-side code. Pages still
works and isn't being killed, but it's in maintenance mode — no new features,
and notably no Queues or Cron Triggers, which the Stripe webhook follow-up
will likely want. Migrating now avoids a second migration later.

Practical effect: `wrangler.jsonc` replaces the implicit Pages project
config; a small Worker entry file handles `/api/*` and falls through to
`env.ASSETS.fetch(request)` for everything else (the existing Hugo `public/`
build, unchanged). The `flowersbytiana.com` custom domain cutover from Pages
to the Worker happens last, deliberately, only after the whole site and the
new form have been verified on the Worker's own `workers.dev` preview URL.

## Marking a painting for sale

The `img` shortcode (`layouts/shortcodes/img.html`) already accepts an
unused optional 3rd argument, rendered as a caption. This is repurposed for
the painting's size, and the "Contact us about this piece" button is coupled
to its presence: a size given means the button renders; no size means the
photo renders exactly as it does today. This requires zero changes to the
173 existing `{{< img >}}` calls — sizes (and therefore buttons) are added
incrementally, per piece, as Tiana decides to list it.

```
{{< img "orchids/orchids-000.jpg" "Orchid 000" "16 × 20 in" >}}
```

The button links to `/contact/?piece=orchids-000&title=Orchid%20000`, where
`piece` is derived from the image filename (path and extension stripped) and
`title` is the existing alt text.

## The contact page

One new page at `/contact/`. A few lines of inline JS read the `piece` and
`title` query params on load and display "Regarding: Orchid 000"; if they're
absent (someone reaches `/contact/` directly), a free-text "which piece are
you asking about?" field is shown instead, to also support general
inquiries.

Fields: name, email (so the reply goes somewhere), message. A Cloudflare
Turnstile widget guards against spam bots — free, non-annoying, and a
natural fit since the rest of the infrastructure is already on Cloudflare.

## The Worker API: `POST /api/contact`

1. Verify the Turnstile token server-side against Cloudflare's siteverify
   endpoint; reject the request if it fails.
2. Validate the submitted fields (non-empty name/message, plausible email).
3. Send an email via **Resend** to Pavel/Tiana's inbox, with the visitor's
   email set as `Reply-To` so replying is a single click. Resend is
   Cloudflare's own currently-recommended path for sending email from
   Workers now that MailChannels' free tier for Workers is gone (sunset
   August 2024); free tier is 3,000 emails/month, far more than a contact
   form needs.
4. Return success/failure to the page's JS, which shows an inline confirmation
   message (no separate "thanks" page needed).

The Resend API key and the Turnstile secret key are stored as Wrangler
secrets (`wrangler secret put`), never committed to the repo — same
discipline already used for `.env` (gitignored) elsewhere in this project.

## Setup required outside the code

- A free Resend account, with `flowersbytiana.com` verified for sending
  (adds a couple of DNS records — straightforward since the domain's DNS is
  already on Cloudflare for the R2 CDN custom domain).
- A free Turnstile site key + secret from the Cloudflare dashboard.

## Out of scope

- Stripe Payment Links / checkout — a separate follow-up spec once this
  ships.
- Any automation for marking a piece "sold" (removing its button/size) —
  manual edit to that photo's `{{< img >}}` call is enough at this scale.
- Rate limiting beyond what Turnstile provides — not worth the complexity
  yet at expected volume.

# GitHub App registration — field-by-field

**Date:** 2026-06-10. The App is the Stage 2 install surface (Journey 2 in
[customer-journeys.md](../product/customer-journeys.md)). Registration is a
~5-minute owner task; the App is a *separate identity under the org* — the
cortex repo stays a normal repo.

## Create the App

Path: `github.com/organizations/autumngarage/settings/apps` → **New GitHub App**

| Field | Value |
|---|---|
| GitHub App name | `Cortex Decision Reviewer` |
| Homepage URL | `https://github.com/autumngarage/cortex` (the #508 landing page later) |
| Webhook URL | `https://cortex-production-61d7.up.railway.app/webhooks/github` (compass service domain, provisioned 2026-06-10) |
| Webhook secret | generate: `openssl rand -hex 32` → store in 1Password |
| Webhook Active | **unchecked** until the #470 API shell is deployed (flip on when told) |
| Repository permissions | Metadata: **Read** · Contents: **Read** · Pull requests: **Read & write** — nothing else (least-privilege per #385: no checks, no admin, no issues write) |
| Subscribe to events | Pull request · Issue comment · Pull request review · Pull request review comment (feedback capture, #393) |
| Where can it be installed | **Any account** (design partners + Marketplace) |

After **Create**:

1. Note the **App ID** → 1Password.
2. **Generate a private key** (.pem downloads) → 1Password.
3. Both later become Railway variables on the compass `cortex` service
   (`GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`)
   for #386's installation auth.

## Marketplace publisher verification (#384 — start immediately)

The external review takes **weeks**; it is the calendar long pole for
Journey 2's payment rail. Prerequisites to file now:

- [ ] Org profile complete (display name, email, logo)
- [ ] Two-factor authentication **required** org-wide
- [ ] Verified domain on the org (pairs with the #508 landing-page domain)
- [ ] Then: App page → *List in Marketplace* → publisher verification flow

Fallback if approval stalls: direct Stripe (#399) — documented, not
preferred.

## Slack app (Stage 3, when #458's skeleton lands)

`api.slack.com/apps` → Create from manifest. The manifest ships with the
#458 Bolt skeleton: Socket Mode on, scopes limited to mentions/DMs
(`app_mentions:read`, `im:history`, `im:read`, `im:write`, `chat:write`),
one dogfood workspace. No passive ingestion scopes — by design.

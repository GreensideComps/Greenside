# Integrations

Four systems the theme is built to accept but does not pretend to have. Each one
has a settings field; while that field is empty the related UI stays hidden
rather than showing something that does not work.

---

## 1. Email and CRM

**Already connected.** The signup forms post to Shopify's customer form, which is
what Klaviyo, Mailchimp and every other major provider syncs from. Nothing needs
building.

Each signup carries tags so you can tell them apart:

```
newsletter, greenside-club, signup-<location>
```

where `<location>` is the section's analytics label — `footer`, `homepage`,
`club`, `winners`, `password`.

An `email_signup` analytics event fires on the success render, with the same
location.

**To connect Klaviyo:** install their Shopify app and sync the customer list. The
forms need no changes.

---

## 2. Live entry counts

**Theme settings → Integrations → Live entry count endpoint.**

Without an endpoint, progress bars show the `entries_sold` and `entries_total`
metafield values, which are accurate at page render. That is the default and it
is honest.

With one, the theme requests live counts once per page load and updates the bars
in place. Expected shape:

```
GET <endpoint>?ids=12345,12346

{
  "12345": { "sold": 6420, "total": 10000 },
  "12346": { "sold": 310,  "total": 2500 }
}
```

Requirements:

- CORS must allow your storefront origin.
- Respond in under about 500ms; the page has already rendered and is not waiting.
- A failure is silent by design — the rendered metafield values stay, because
  they are still correct.

The client clamps `sold` to `total` and ignores any entry missing either number,
so a malformed response degrades to the metafield values rather than to nonsense.

---

## 3. Referrals

**Theme settings → Integrations → Referral link base URL** and **query
parameter**.

While the base URL is empty the referral section says invites are coming and
offers nothing else. It does not show a link that will not work.

Once set, a logged-in customer sees:

```
<referral_base_url>?<referral_query_param>=<customer.id>
```

A logged-out visitor is asked to log in first, because there is no identity to
attribute a referral to.

**To connect a provider:** point the base URL at their landing endpoint. If they
issue their own codes rather than accepting a Shopify customer id, add a customer
metafield for the code and change the one `assign referral_link` line in
`sections/referral.liquid`. Nothing else moves.

`referral_click` and `referral_share` events fire either way.

---

## 4. The Greenside Club

**Not connected, and the section is honest about it.** It presents what the Club
is and captures email through the same Shopify form as everywhere else. Member
tiers, points and status are deliberately absent until there is a real system
behind them.

When a loyalty provider is added, member state belongs in customer metafields,
and `sections/club-tiers.liquid` is where it renders. The markup does not need
rebuilding.

---

## 5. Entry numbers

The one genuine gap, and worth being clear about.

Entry numbers are issued by whatever runs your draw, not by Shopify. The theme
does not invent them. The account area and the order page both point the customer
at where they really are, and the cart reassurance text says they are emailed on
purchase.

To bring them on-site, write them to an order metafield when the draw system
issues them, and render that metafield in `sections/main-order.liquid` where the
note currently sits. That is a backend change, not a theme change — but the place
for it exists and is marked.

---

## Personalisation

`sections/related-competitions.liquid` uses Shopify's Product Recommendations
API. To replace it with your own ranking, point the section's `data-url` at an
endpoint returning the same section markup. Every card, badge and progress bar
keeps working, because they are rendered by the same Liquid.

Recently viewed competitions are stored in the visitor's own browser and never
leave it. The section asks Shopify to render a real card for each handle, so
prices and availability are always live rather than cached from the earlier
visit.

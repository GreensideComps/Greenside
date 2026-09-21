# Competition data model

How the theme decides what a competition is doing, and what it reads to do it.
Every rule below is implemented in `snippets/competition-badges.liquid`,
`snippets/competition-progress.liquid`, `snippets/competition-closing.liquid`
and `sections/main-competition.liquid`.

## Entry states

| State | Condition | What the customer sees |
|---|---|---|
| **Open** | `product.available` and either no closing date or a closing date in the future | Entry selector and the entry button |
| **Closing soon** | Open, and `closing_at` is within 48 hours | A "Closing soon" badge alongside the real date |
| **Fully entered** | `product.available` is false because every entry has been bought | "This competition is fully entered", with links onward |
| **Closed** | `closing_at` has passed | "This competition has closed", with links onward |
| **Unavailable** | Not available for any other reason, such as a draft product | "This competition isn't open yet" |

Closing and draw dates are compared as calendar days in the shop's timezone, so
"Closing today" means today's date, not "within 24 hours".

## Product metafields

All optional, all in the `custom` namespace. **Each display is skipped entirely
when its metafield is empty** — there is no placeholder fallback anywhere, so
the theme cannot show a prize value, an entry count or a closing date that does
not exist.

| Key | Type | Drives |
|---|---|---|
| `closing_at` | `date_time` | Closing date, "closing soon" badge, closed state |
| `draw_at` | `date_time` | Draw date line |
| `entries_total` | `number_integer` | The published entry cap. Denominator for availability and for the competition-page progress bar |
| `entries_sold` | `number_integer` | **Competition-page progress bar only. Never customer-facing availability** — see below |
| `prize_value` | `money` | Prize value on cards and the competition page |
| `cash_alternative` | `money` | Cash alternative on the competition page |
| `max_entries_per_person` | `number_integer` | Caps the quantity input and preset list |
| `entry_bundles` | `list.number_integer` | Preset entry amounts, e.g. `[1, 5, 10, 25]` |
| `instant_win` | `boolean` | "Instant win" badge |
| `competition_type` | `single_line_text` | Eyebrow above the title, e.g. "Live draw" |
| `skill_question` | `single_line_text` | The skill question shown before checkout |
| `skill_answers` | `list.single_line_text` | Multiple-choice answers; omit for a free-text answer |
| `whats_included` | `rich_text` | "What's included" accordion |
| `how_drawn` | `rich_text` | "How the winner is drawn" accordion |
| `eligibility` | `rich_text` | "Terms and eligibility" accordion |
| `badge_label` | `single_line_text` | One merchant-defined badge |
| `free_entry_url` | `url` | Per-competition free entry route, overriding the theme setting |

The progress bar needs **both** `entries_total` and `entries_sold`, and
`entries_total` must be greater than zero. Anything less and no bar renders.

### Availability comes from inventory, not from `entries_sold`

`entries_sold` is hand-maintained and nothing writes to it. It still read `0`
while a real paid order existed against a competition. Any customer-facing
figure built on it is therefore wrong as soon as the business is trading.

Customer-facing availability — on cards, in the hero, anywhere a visitor makes
a decision — is derived from live Shopify inventory instead, in
`snippets/competition-availability.liquid`:

```
remaining = variant.inventory_quantity
taken     = entries_total - variant.inventory_quantity
```

Inventory is decremented transactionally by Shopify on every order and the cap
is enforced against it at checkout, so it is the only figure that is right
without anyone remembering to update it. Nothing renders unless `entries_total`
is above zero and the variant is tracked by Shopify.

**Do not build a second availability system.** If a figure is needed somewhere
new, render this snippet.

### How each order path moves inventory

The two paths do not behave identically, and the difference is the reason the
figures are clamped.

| Path | Decrements inventory | Respects the `DENY` cap |
|---|---|---|
| Customer checkout | Yes | **Yes** — refused with `MERCHANDISE_OUT_OF_STOCK` |
| Postal entry (Draft Order) | Yes | **No** — verified overselling to `-1` |

A postal entry accepted after the cap is reached drives inventory negative. So
`inventory_quantity` can legitimately be negative, and the availability snippet
clamps `remaining` to zero and reports **"Fully entered"** rather than quoting
a negative number. It also clamps at the top, for a restock or a raised cap.

Clamping stops a customer seeing something absurd. **It does not fix the
oversell** — the entry still exists and still has to be honoured or resolved.
That is why capacity must be checked before a postal entry is processed, per
the constraint above.

## Variant metafields

| Key | Type | Drives |
|---|---|---|
| `entries` | `number_integer` | How many entries one unit of this variant contains |

Only needed when a competition uses bundle variants. See "Entry selection" below.

## Entry selection

The theme supports two models and picks automatically:

**Quantity mode** — the product has one variant. The line quantity *is* the
number of entries. Preset amounts come from `entry_bundles`. Because the
per-entry price is identical at every preset, no "best value" badge is shown:
labelling one as better would not be true.

**Bundle mode** — the product has more than one variant. Each variant is a
bundle, and `custom.entries` on the variant says how many entries it contains.
The theme calculates the per-entry price from the variant's real Shopify price
and displays it, so the comparison is arithmetic on live data. "Best value" is
applied automatically to whichever variant genuinely has the lowest per-entry
price, and is omitted when every variant works out the same per entry.

## Badge priority

Badges are capped by the theme setting "Maximum badges per card" so a card never
becomes badge soup. When more apply than fit, the highest priority survive:

1. Closed / fully entered — blocks entry, so it must always show
2. Closing soon — a real date within 48 hours
3. Instant win — changes the mechanic
4. Merchant badge (`badge_label`)
5. Featured, then New — driven by product tags `featured` and `new`

## Tags the theme understands

| Tag | Effect |
|---|---|
| `featured` | "Featured" badge |
| `new` | "New" badge |

Tags are only used for these marketing badges. Anything that affects entry
mechanics comes from a metafield, so it is structured and reportable.

## Two settings that make a claim about your draw

Both ship **off**, because each asserts something about how Greenside operates
that the storefront must not say before your rules do. See `docs/PERSUASION.md`
for the full reasoning.

| Setting | What it publishes | Turn it on when |
|---|---|---|
| `show_odds` | "1 in 4,000 — at the full entry cap" on the competition page | Your competition rules state the draw is made from the full set of issued entry numbers |
| `entry_numbers_issued` | "Your entry numbers are issued with that confirmation" | Something actually allocates entry numbers and sends them |

`show_odds` divides `custom.entries_total` by the entries being considered —
never `entries_sold`, which would flatter the figure and drift upward as the
competition fills. Dividing by the published cap is the worst case for the
entrant, so a real chance can only be better than the number shown.

`entry_numbers_issued` exists because nothing in Shopify allocates entry numbers
by default. Allocation needs Shopify Flow or a small app writing numbers to an
order metafield or line item property. Until that exists, leaving this off keeps
the storefront from promising a number the entrant never receives.

## Postal entries — two verified constraints

Postal entries ("no purchase necessary") are processed as **£0 Shopify orders**
through the Draft Order workflow, so they share the customer record, order
system, competition identification, entry allocation and draw pool with paid
entries. There is deliberately no separate postal-entry database.

Both rules below come from tests run against the live store on 2026-09-21, not
from documentation. The evidence is recorded in `docs/QA.md`.

### 1. Draft orders bypass the inventory cap — check capacity before processing

Shopify's entry cap is enforced by variant inventory set to **`DENY` + tracked**.
That enforcement is real for customers: a checkout whose stock has gone is
refused with `MERCHANDISE_OUT_OF_STOCK`, even when the cart was built while
stock still existed.

**It does not apply to draft orders.** Two draft orders completed in parallel
against a stock of 1 both succeeded, leaving inventory at `available: -1`,
`committed: 2`, `onHand: 1`. Shopify allows this by design, on the basis that a
merchant creating an order manually knows what they are doing.

Postal entries are draft orders. So the one route that is free to enter is also
the only route that can push a competition past its published cap, silently and
with no warning.

> **Before processing a postal entry, check the competition's remaining
> capacity.** Never process a batch blindly.

Policy for postal entries received *after* the cap is reached is a legal and
fairness question, not a technical one, and is to be settled with Ben's adviser.
Do not build an automated workaround until that policy exists — the correct
behaviour (reject, refund-equivalent, roll to the next competition, or raise the
cap) changes what the code should do.

### 2. Use a 100% line-item discount, not an order-level discount

Both placements produce a £0 order total. They do not produce the same record.

| Discount placement | Order total | Line item `discountedTotal` |
|---|---|---|
| Order level | £0.00 | **£2.49** — wrong |
| **Line item level** | £0.00 | £0.00 — correct |

An order-level discount leaves the line item still claiming the full entry
price, so anything reading line items — revenue reporting, entry-value figures,
reconciliation — will count a free entry as a paid one.

Apply the discount to the **line item**:

```
lineItems: [{
  variantId: "...",
  quantity: 1,
  appliedDiscount: { title: "Postal entry", value: 100, valueType: PERCENTAGE },
  customAttributes: [{ key: "Skill answer", value: "..." }]
}]
```

### What a correct postal entry order looks like

Verified on a real £0 order:

- Total **£0.00**, `financial_status: paid`, and **no transaction record** — so
  an allocator filtering on `financial_status == paid` picks it up alongside
  paid entries, which is what we want.
- Tagged `postal-entry`.
- Line item properties carry the skill answer, the question asked, and
  `_entry_route: postal`.
- Order attributes carry the date received, the date processed and who
  processed it; the order note carries the full audit trail.
- **Inventory decrements**, so a postal entry consumes one of the published
  entries — correct, and the reason constraint 1 above matters.


## Entry properties — the canonical shape

Both entry routes write the same three line-item properties. They were not
always identical: the storefront once wrote a single property keyed on the
section heading (`Skill question: Bunker`) that held the answer and never
recorded the question, while the postal route wrote the documented shape. Two
routes producing two order shapes is a bug in the audit trail, not a detail.

| Property | Value | Visible to the customer |
|---|---|---|
| `Skill answer` | The answer the entrant gave | Yes — cart, checkout, order, confirmation |
| `_skill_question` | The exact question text they were shown | No |
| `_entry_route` | `online` or `postal` | No |

The leading underscore is Shopify's convention for a property hidden from cart
and checkout, so the answer is visible for the entrant to check and the
bookkeeping is not.

**The question text is carried on the order, not looked up afterwards.** The
`skill_question` metafield can be edited while entries exist, so resolving it
later would describe the question as it is now rather than as the entrant saw
it. An order has to be able to prove what it was actually asked.

## The per-person limit is not enforceable

`custom.max_entries_per_person` still caps the quantity input, but it is **not
stated to the customer anywhere**, because it cannot be held to.

Verified against the live store: a direct `POST /cart/add.js` ignores it
completely. Two QA competitions with caps of 50 and 25 both accepted exactly
50 — that 50 is a Shopify platform default, not the metafield. A competition
capped at 25 took 50 entries.

Enforcing it requires validation between cart and order, which on Shopify
Basic means Shopify Functions, which requires Plus. Until then the storefront
claims nothing it cannot enforce. The metafield is kept so the cap can be
checked post-payment alongside the skill answer, and so the claim can be
restored the moment it becomes true.

## Allocator eligibility

An order is eligible for the draw when **both** are true:

```
financial_status == paid  AND  cancelled_at == null
```

The second condition is not optional. A cancelled order **keeps** its
financial status: QA order #1003 was cancelled and still reports
`financial_status: paid`. Filtering on payment alone would put cancelled and
refunded entries into the draw pool.

Two further notes for whoever builds the allocator:

- A **manual payment method creates orders as `pending`, not `paid`**, and they
  become paid only when someone marks them so in admin. The allocator must
  read status at the point it runs, not assume it at the point of order.
- **Wrong skill answers are not blocked before payment.** Shopify Basic has no
  pre-payment validation, so eligibility is checked after the fact: the
  allocator compares each order's `Skill answer` against the competition's
  correct answer, and excludes the ones that do not match. This is the
  documented and intended design, not a gap to be closed at the cart layer.

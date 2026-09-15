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
| `entries_total` | `number_integer` | Progress bar denominator |
| `entries_sold` | `number_integer` | Progress bar numerator |
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

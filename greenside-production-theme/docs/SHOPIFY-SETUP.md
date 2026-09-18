# Shopify setup

Everything the theme reads, and exactly how to create it. The theme renders
correctly with none of this in place — each display is skipped when its data is
absent, rather than filled with a placeholder — but it is designed around this
structure.

Work top to bottom. The whole thing takes about an hour.

---

## 1. Product metafields

**Settings → Custom data → Products → Add definition.** Namespace and key must be
exactly `custom.<key>`.

| Key | Type | Drives |
|---|---|---|
| `closing_at` | Date and time | Closing date, "closing soon" badge, closed state |
| `draw_at` | Date and time | The draw date line |
| `entries_total` | Integer | Progress denominator |
| `entries_sold` | Integer | Progress numerator |
| `prize_value` | Money | Prize value on cards and the competition page |
| `cash_alternative` | Money | Cash alternative on the competition page |
| `max_entries_per_person` | Integer | Caps the quantity input and the preset list |
| `entry_bundles` | List of integers | Preset entry amounts, e.g. `1, 5, 10, 25` |
| `instant_win` | True or false | "Instant win" badge |
| `competition_type` | Single line text | Eyebrow above the title, e.g. "Live draw" |
| `skill_question` | Single line text | The question shown before checkout |
| `skill_answers` | List of single line text | Multiple choice answers. Omit for a free-text answer. |
| `whats_included` | Rich text | "What's included" accordion |
| `how_drawn` | Rich text | "How the winner is drawn" accordion |
| `eligibility` | Rich text | "Terms and eligibility" accordion |
| `badge_label` | Single line text | One merchant-defined badge |
| `free_entry_url` | URL | Per-competition free entry route, overriding the theme setting |

Tick **Storefronts** on every definition, or the theme cannot read it.

The progress bar needs **both** `entries_total` and `entries_sold`, with a total
above zero. Anything less and no bar renders — there is deliberately no fallback.

### Variant metafield

Only needed if you sell bundles as variants rather than by quantity.

| Key | Type | Drives |
|---|---|---|
| `entries` | Integer | How many entries one unit of this variant contains |

---

## 2. How to price entries

Two models. The theme detects which you are using and adapts.

**Quantity mode (recommended).** One variant per competition. The line quantity
*is* the number of entries. Set `entry_bundles` to the presets you want, e.g.
`1, 5, 10, 25`. Because every preset costs the same per entry, no "best value"
badge appears — saying otherwise would not be true.

**Bundle mode.** Several variants, each a bundle, each with `custom.entries` set.
The theme calculates the per-entry price from each variant's real Shopify price
and displays it, so the comparison is arithmetic on live data. "Best value" is
applied automatically to whichever variant genuinely has the lowest per-entry
price, and omitted entirely when they all work out the same.

---

## 3. Metaobjects

**Settings → Custom data → Metaobjects → Add definition.**

### `winner`

Type must be exactly `winner`. Enable **Storefronts** access.

| Field key | Type | Notes |
|---|---|---|
| `name` | Single line text | "Danny R." is enough; full names are not needed |
| `prize` | Single line text | What they won |
| `competition` | Product reference | Links back to the competition |
| `drawn_on` | Date | Draw date |
| `photo` | File reference | Portrait, 4:5 works best |
| `story` | Multi-line text | Two sentences in their own words |
| `video_url` | URL | The draw recording, if there is one |
| `location` | Single line text | Town or city |

Every field is optional. A winner with only a name and a prize still renders
cleanly. The winners page pages through them in the browser, so it scales to
hundreds without a slow first render.

### `faq`

| Field key | Type |
|---|---|
| `question` | Single line text |
| `answer` | Rich text |

Use this when the same questions appear on more than one page. The FAQ section
emits `FAQPage` structured data from real question and answer pairs only.

---

## 4. Collections

| Handle | Purpose |
|---|---|
| `featured-competitions` | The four shown on the homepage |
| `closing-soon` | Optional, for a dedicated listing |
| `all` | Created automatically by Shopify |

Plus one per prize category — `drivers`, `putters`, `tech`, `experiences` and so
on. These feed the homepage category tiles and the navigation.

A smart collection with the condition **Product tag is equal to `featured`** is
the simplest way to manage `featured-competitions`.

---

## 5. Filters

Install **Shopify Search & Discovery** (free, first-party) and add filters for
the metafields you want to filter by — prize category, instant win, entry price.
The theme shows exactly the filters this app provides and no others, so it can
never offer a filter your catalogue cannot answer.

---

## 6. Navigation

**Content → Menus.**

`main-menu`:

- Competitions → sub-items for each category collection
- Closing soon
- Winners
- How it works
- The Club

`footer`: All competitions, Winners, How it works, Competition rules, Free entry
route, Contact.

`search-shortcuts`: your three or four most popular categories. These appear in
the search panel before anyone types.

---

## 7. Pages

| Handle | Template | Notes |
|---|---|---|
| `winners` | `page.winners` | Reads the `winner` metaobject |
| `how-it-works` | `page.how-it-works` | Seven steps, pre-written |
| `contact` | `page.contact` | Shopify contact form |
| `competition-rules` | `page` | **Needs legal sign-off** |
| `free-entry` | `page` | **Needs legal sign-off** — see below |
| `about` | `page` | Who you are. Put a real name and face on it. |

Assign a template under **Online store → Theme template** when editing the page.

---

## 8. Theme settings to fill in before launch

**Customise → Theme settings.**

- **Brand** — logo, light logo (for the dark header and footer), favicon,
  sharing image.
- **Legal** — footer legal text with your company number and registered address,
  competition rules URL, free entry route URL, minimum age.
- **Social accounts** — only the ones you actually have; empty fields render
  nothing rather than a dead icon.
- **Integrations** — leave the entry-progress endpoint and referral base URL
  empty until those systems exist. The related UI stays hidden while they are.

---

## 9. Things the theme deliberately leaves to you

These are flagged rather than guessed, because getting them wrong has legal or
trust consequences:

- **The free entry route.** UK prize competitions need a genuinely accessible
  free route, and the theme links to a page it expects you to write with legal
  advice. The announcement bar currently states that every competition has one —
  make sure that is true before launch.
- **Responsible participation.** Competitors carry GamCare links. The theme has
  a place for a stance; what it should say is a question for your adviser.
- **Any statement of odds.** The theme publishes entry caps, which is factual. It
  does not compute or claim odds.
- **Company identity.** Company number, registered address and a named human.
  The slots exist and are empty; anonymity is the single most common reason a
  visitor decides a competition site is a scam.

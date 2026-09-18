# Greenside — production Shopify theme

A standalone Online Store 2.0 theme for Greenside Competitions. It does not
depend on, extend or modify the pre-launch theme, and installs alongside it as
an unpublished theme so it can be previewed before it goes anywhere near the
live site.

---

## The idea in one paragraph

Every UK competition site is converging on the same thing: countdown timers,
percentage-sold meters, spin-to-win, instant win, gold bars filling up. They are
lottery operators with golf inventory, and at least one of them carries a GamCare
link in its own footer. This theme is built on the opposite bet — that a golf
brand which happens to run competitions, and which makes the fairness of the draw
the most visible thing it owns, is both more defensible and more pleasant to use.
Most of the decisions below only make sense in that light.

---

## Install

1. Download `greenside-production-theme.zip`.
2. Shopify admin → **Online Store → Themes → Add theme → Upload zip file**.
3. It installs unpublished. Use **Customise** to preview and configure.
4. Publish only when you are ready.

Requires no apps. Filtering uses Shopify's own **Search & Discovery** app, which
is free and first-party; without it the collection page simply shows no filters.

---

## Architecture

```
layout/      theme.liquid, password.liquid
templates/   JSON templates, including customers/
sections/    30 sections + header-group.json / footer-group.json
snippets/    30 reusable partials
blocks/      theme blocks for the flexible-content section
assets/      CSS and dependency-free JavaScript
config/      settings_schema.json, settings_data.json
locales/     en.default.json, en.default.schema.json
docs/        this documentation
tests/       node:test suites (not shipped in the ZIP)
```

No build step. No framework. No npm dependency at runtime. The JavaScript is
custom elements; every one of them is an enhancement over markup that already
works without it.

---

## The design system

### Colour — five roles, one job each

| Role | Default | Used for |
|---|---|---|
| **Ink** | `#0C1F18` | The darkest ground: hero, major moments |
| **Green** | `#1B4332` | The brand ground |
| **Action** | `#417B25` | The thing you click. Nothing else. |
| **Paper** | `#F2F3EF` | The page |
| **Brass** | `#C9A961` | Winners and achievement. Nothing else. |

Brass appears on no progress bar, no urgency badge and no button. A gold meter
filling up is the most recognisable cue in online gambling, and this is a golf
brand. On light grounds brass is darkened 42%, which is the measured point at
which it clears 4.5:1 against white, paper and the muted sage surface.

Foreground colours are chosen at render time with Liquid's `color_contrast`,
not `color_brightness` — the latter over-weights green and had previously
declared a mid-tone green dark enough for white text when the real ratio was
4.14:1.

### Typography — three faces, three jobs

| Role | Default | Why |
|---|---|---|
| Display | **Instrument Serif** | Editorial and confident. The thing that makes the site recognisable with the logo removed. |
| Body | **Inter** | Invisible by design. |
| Numbers | **IBM Plex Mono** | Entry counts, prices, dates and draw numbers. Figures that line up read as precise and checkable — which is the proposition. |

All three are in Shopify's hosted font library: no licence to buy, no
self-hosted binaries, no external request, and a merchant can change any of them
from the theme editor.

Uppercase is confined to small labels. A test enforces this by inspecting the
font-size of every CSS rule that sets `text-transform: uppercase`.

### Spacing — three tiers

`major` (1.75×), `standard` (1×) and `minor` (0.5×), all derived from one
setting. Sections choose a tier for their top and bottom; none invents its own
number. Uniform rhythm is what makes a page read as a list rather than a
composition.

---

## What the theme will not do

These are enforced by tests, not by convention:

- No countdown timers.
- No progress bar that changes colour as it fills.
- No more than one badge on a card.
- No fabricated scarcity, entry counts, winners, ratings or reviews. Every
  number on the site comes from Shopify or a product metafield, and any display
  whose metafield is empty is skipped rather than filled with a placeholder.
- No `capitalize` on a product title — it lowercases everything after the first
  character and turns "TaylorMade Qi10" into "Taylormade qi10".

---

## Setup

The theme renders correctly on an empty store, but it is designed around a small
amount of structured data. See:

- **[docs/SHOPIFY-SETUP.md](docs/SHOPIFY-SETUP.md)** — metafields, metaobjects,
  collections, navigation and pages, with the exact definitions to create.
- **[docs/COMPETITION-DATA-MODEL.md](docs/COMPETITION-DATA-MODEL.md)** — how
  entry states, badges and progress are decided.
- **[docs/ANALYTICS.md](docs/ANALYTICS.md)** — the event set, and how to consume it.
- **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)** — where a CRM, a loyalty
  provider, a referral provider and a live entry-count feed plug in.
- **[docs/QA.md](docs/QA.md)** — what was tested, how, and what the results were.

---

## Testing

```bash
node --test tests/*.test.js     # 62 tests, no dependencies
shopify theme check             # 0 offenses
```

The suites cover the money formatter, the analytics layer's deduplication,
browser-storage degradation, theme integrity (every template, snippet, asset and
translation key resolves), and the design system's own rules.

---

## Known external dependencies

- **Shopify Search & Discovery** for collection filters. Free, first-party,
  optional.
- **Shopify hosted fonts** for all three typefaces.
- Nothing else. No CDN, no third-party script, no tracking pixel. A test asserts
  that no external script or stylesheet host is hard-coded anywhere in the theme.

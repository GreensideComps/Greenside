# QA report

Last run: 2026-09-18. Re-run everything below with `node --test tests/*.test.js`
and `shopify theme check`.

---

## Automated tests — 62/62 passing

No dependencies; runs on `node:test`.

| Suite | Covers |
|---|---|
| `money.test.js` | The one piece of pricing logic that runs client-side: the live entry total. Every Shopify money placeholder, thousands separators, non-UK separator formats, bad input, and entry-count multiplication at realistic volumes. |
| `analytics.test.js` | Events deduplicate within the window, stay completely silent when disabled, survive an unserialisable payload without throwing, and emit `page_view` exactly once. |
| `recently-viewed.test.js` | Storage cap, deduplication, corrupted content, and silent degradation when `localStorage` throws — as it does in private browsing. |
| `theme-integrity.test.js` | Every JSON template and section group points at a section that exists; every rendered snippet exists; every `asset_url` ships; every schema setting has a label; every translation key resolves; no credentials; no external script host; no filter inside a `t:` argument; every image has `alt`; every button has an accessible name. |
| `design-system.test.js` | The audit's rules, enforced: brass never on progress, urgency or actions; no percentage-driven fill colour; one badge maximum; no `capitalize` on a name; uppercase confined to small labels; three distinct spacing tiers; the card carries no container chrome; the grid is two-up on phones; the five colour roles hold their values. |

---

## Shopify Theme Check — 0 offenses

```
shopify theme check
```

Run with the CLI's `theme-check:recommended` ruleset. Zero errors, zero
warnings, zero suggestions across every file.

---

## Browser QA

17 page states rendered through a local Liquid harness and driven in real
Chromium with the production typefaces loading.

**Pages:** homepage · competition (open, bundle variants, fully entered, closed,
and one with no metafields at all) · collection · empty collection · cart · empty
cart · search results · no search results · winners · how it works · 404 · login
· contact

**Widths:** 320, 390, 768, 1280, 1440

| Check | Result |
|---|---|
| axe-core, WCAG 2.2 AA | **0 violations** |
| Horizontal overflow | **None** at any width |
| Console errors | None from the theme |
| Missing translations | 0 |

The only console entries are 404s for Shopify's recommendation endpoints, which
do not exist in a static harness. The theme handles them correctly by hiding the
section — which is the behaviour being verified.

---

## Performance

| | |
|---|---|
| Mobile homepage scroll length | **7,159px** — down from 11,520px |
| Screens of scroll at 390×844 | **8.5** — down from 29 |
| External requests | **None.** No CDN, no third-party script, no tracking pixel. |
| JavaScript | ~34KB unminified across 9 files, no framework, no build step |
| Fonts | 3 faces from Shopify's own CDN |
| Images | `srcset` capped at each image's real width so Shopify is never asked to upscale; hero eager with `fetchpriority="high"`, everything else lazy |

---

## Defects found and fixed during QA

Recorded because several were invisible to static analysis and only appeared once
the theme was rendered in a browser.

| Defect | How it was found |
|---|---|
| The cart drawer and search modal never matched their own CSS, and were rendering in the page flow pushed off-screen on every page. Overlay styles were bound to the `gs-overlay` element name, which subclassed elements do not inherit in CSS. | Horizontal overflow check |
| The inherited accent `#4a8c2a` fails WCAG AA at 4.14:1 against both white and dark text, so no button label on it could pass. | Measured contrast |
| `value \| default: 'some.key' \| t` ran the translation over the merchant's own text. Eight occurrences would have printed "translation missing". | Render harness |
| A filter inside a `t:` argument pipes the translated string into the filter, not the argument. Ten occurrences, including a footer that read "© now" instead of the year. | Visual inspection, then grep |
| Liquid's `capitalize` lowercases everything after the first character, turning "TaylorMade Qi10" into "Taylormade qi10". | Visual inspection |
| The homepage had no `h1` when no featured competition was selected — the freshly installed state. | Markup inspection |
| The hero sits on a dark overlay but outside a colour scheme, so secondary buttons rendered dark on dark. | Screenshot |
| `margin-inline: auto` makes a grid item shrink to content width rather than stretch, silently narrowing any page-width wrapper in a grid cell. | Screenshot, then computed styles |
| Prize values were read as pence, showing £529 as £5. | Screenshot |
| Cards with a closing date sat their footers on a different baseline to those without. | Screenshot |
| Card CTA overflowed at 320px in a two-up grid. | Overflow check |
| Brass at 22% darkening still failed on light grounds; 42% is the measured passing point. | axe-core |

Each of the recurring classes now has a test that fails if it returns.

---

## Not covered

- **Real Shopify rendering.** The harness implements enough of the Liquid
  environment for faithful visual QA, but it is not Shopify. Preview the
  uploaded theme before publishing.
- **Real checkout.** Shopify's checkout is out of the theme's control.
- **Screen reader testing by a human.** axe-core catches programmatic failures,
  not confusing ones. Worth a pass with VoiceOver or NVDA before launch.
- **Field performance.** Run Lighthouse against the previewed theme with real
  images, which are the variable that matters most.

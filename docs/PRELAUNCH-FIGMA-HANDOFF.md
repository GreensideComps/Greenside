# Pre-launch page — Figma design handoff

Specification of the approved **`04 Pre-launch page`** (Direction A — Yardage Book),
written so the page can be reproduced in Shopify without further Figma calls.

- **Status:** structure and direction approved for implementation (25 Sep 2026).
  Nothing has been built in Shopify yet.
- **Source of truth:** Figma file `Greenside — Pre-launch`,
  <https://www.figma.com/design/tt2w7BD7dB1OTDlVwXqTxa> (in the owner's drafts).
- **Every value below is taken from the Figma build.** Anything that was not
  designed is marked **Proposed** or **To verify**. Do not reinterpret the design.

| Figma node | What it is |
|---|---|
| `9:2` | Pre-launch — Mobile 390 |
| `10:2` | Pre-launch — Desktop 1440 |
| `11:2` | Signup — states (mobile 390): default, error, success |
| `11:49` | Section wrapping all of page 04 |
| `3:2` | 01 Foundations reference sheet |
| `6:11` | 03 Directions — **superseded explorations, not the design** (see §8) |

The file is on the Figma Starter plan: max 3 pages, and the monthly MCP call
limit was reached on 25 Sep 2026. Treat this document as the working reference
until the limit resets.

---

## 1. Design system

### 1.1 Colour

Brand roles:

| Token | Hex | Role |
|---|---|---|
| `brand/ink` | `#0C1F18` | Deepest background (signup band, footer); text on paper |
| `brand/green` | `#1B4332` | Brand surface: header, hero, final CTA |
| `brand/paper` | `#F2F3EF` | Light surface (content sections); contour strokes |
| `brand/action` | `#417B25` | **Interactive elements only** (buttons). Never badges, bars or headings |
| `brand/brass` | `#C9A961` | **Winners only. Not used anywhere on the pre-launch page** |

Support colours (derived):

| Token | Hex | Use |
|---|---|---|
| `support/white` | `#FFFFFF` | Inputs; text on green/ink |
| `support/ink-muted` | `#4A5A52` | Secondary text on paper; input placeholder |
| `support/on-dark-muted` | `#B7C7BD` | Secondary text on green/ink |
| `support/rule-on-paper` | `#D6DAD2` | Hairlines on paper; photo-placeholder fill |
| `support/rule-on-dark` | `rgba(255,255,255,0.18)` | Hairlines on green/ink |
| `support/error-on-dark` | `#F2B8B5` | Validation text and borders on green/ink. Errors only |
| `support/error-on-paper` | `#92262B` | Validation on paper; also the placeholder warning note |

Measured contrast (WCAG):

| Pair | Ratio |
|---|---|
| White on green | 11.08:1 |
| On-dark-muted on green | 6.29:1 |
| On-dark-muted on ink | 9.73:1 |
| White on action (button label) | 5.14:1 |
| Ink on paper | 15.38:1 |
| Ink-muted on paper | 6.55:1 |
| Error-on-dark on ink | 10.04:1 |
| Error-on-paper on paper | 7.43:1 |

The action button against the green hero background is 2.15:1. That is
acceptable because the white label identifies the control, but don't rely on
the button's edge alone anywhere else.

### 1.2 Typography

Three families. All are SIL Open Font License.

- **Bricolage Grotesque**: headlines and the wordmark.
- **Inter**: body text and buttons.
- **IBM Plex Mono**: labels and data.

Use sentence case everywhere. Uppercase appears only in the wordmark
("GREENSIDE"). Mono labels are sentence case, e.g. "Join the waitlist",
"Green 00 · Pre-launch".

| Style | Family / weight | Size | Line height | Tracking | Used for |
|---|---|---|---|---|---|
| Display/XL | Bricolage Grotesque Bold (700) | 52 | 100% | −2% | Mobile H1 |
| Display/Desktop XL | Bricolage Grotesque Bold (700) | 80 | 98% | −2.5% | Desktop H1 |
| Display/L | Bricolage Grotesque SemiBold (600) | 36 (set at **30** on mobile) | 110% | −1.5% | Mobile section headings |
| Display/Desktop L | Bricolage Grotesque SemiBold (600) | 44 (set at **36** in the desktop signup band) | 108% | −1.5% | Desktop section headings |
| Body/L | Inter Regular (400) | 18 (desktop hero lede **19**) | 155% | 0 | Ledes; intro copy on desktop |
| Body/M | Inter Regular (400) | 16 | 150% | 0 | Body copy; input text |
| Body/S | Inter Regular (400) | 14 | 145% | 0 | Consent label, notes, helper text |
| UI/Button | Inter Semi Bold (600) | 16 | 150% | 0 | Buttons. Also item titles at 17 (mobile) / 20 (desktop), and accordion titles at 16 / 18 |
| Label/Mono | IBM Plex Mono Medium (500) | 11 | 145% | +4% | Eyebrow labels, small print |
| Data/Mono | IBM Plex Mono Regular (400) | 13 | 150% | 0 | Numbers ("01"), slope notes. Also at 14–16 for detail values and the category line, and 18–20 for accordion "+" |
| Wordmark | Bricolage Grotesque SemiBold (600), UPPERCASE | 15 mobile header · 17 desktop header · 14 mobile footer · 16 desktop footer | auto | **+14%** | "GREENSIDE" |
| Header link | Inter Semi Bold (600), underlined | 14 | 145% | 0 | "Join the waitlist" in the header |

Archivo Light ("Display/Editorial") exists in the Figma file but is **not used**
on page 04.

### 1.3 Spacing, radius and rules

- **Spacing scale (px):** `4 · 8 · 12 · 16 · 24 · 32 · 48 · 64 · 96 · 128`.
- **Rhythm tiers:** hero and major moments 96–128; standard sections 64–72
  (mobile) or 104–120 (desktop); supporting content 24–32.
- **Corner radius:** **2px** on inputs, buttons, checkboxes and the photo
  placeholder. No rounded cards and no radius on sections. The success tick is
  a 40px circle, the only round element.
- **Rules:** 1px hairlines only (`rule-on-paper` on paper, `rule-on-dark` on
  green/ink). The one exception is the desktop "What Greenside is" note
  columns, which use a 1px **ink** top rule.
- **No shadows. No gradients.** A gradient is allowed only as a photographic
  scrim, and there is no photography on this page.

### 1.4 Grids and breakpoints

| | Mobile | Desktop |
|---|---|---|
| Artboard | 390 wide (first viewport 390 × 844) | 1440 wide |
| Columns | 4 | 12 × 64px, centred |
| Margins / side padding | 24 | 160 (so content width is **1120**) |
| Gutter | 16 | 32 |

Only 390 and 1440 were designed. **Proposed** breakpoints for implementation:

- `< 750px`: mobile layout (single column, 24px side padding).
- `750–1099px`: single column, side padding 48, desktop type sizes scaled down
  (H1 around 64). Two-column rows stack.
- `≥ 1100px`: desktop layout, side padding `max(32px, (100vw − 1120px) / 2)`,
  content capped at 1120.

---

## 2. Header

| | Mobile | Desktop |
|---|---|---|
| Background | green | green |
| Height (as built) | 64 | 75 |
| Padding | 20 vertical, 24 sides | 24 vertical, 160 sides |
| Layout | `space-between`, vertically centred | same |
| Left | Wordmark "GREENSIDE", 15px, white | Wordmark, 17px, white |
| Right | Header link "Join the waitlist" (Inter Semi Bold 14, `paper`, underlined) | Group, gap 32: mono label "Pre-launch · Waitlist open" (`on-dark-muted`) + the same header link |
| Divider | 1px `rule-on-dark` bottom rule | same |

- The wordmark is the page's primary logo treatment. It is a **presentation
  treatment only**, not a brand redesign.
- The illustrated golf-ball logo is kept as a secondary/reference asset and is
  **not used on this page**.
- There is no navigation menu. The header link anchors to the signup section
  (`#join`).

---

## 3. Hero

### 3.1 Copy (exact)

- **Eyebrow (mono):** `Green 00 · Pre-launch`. On mobile it is drawn inside
  the contour illustration; on desktop it sits above the H1.
- **H1:** `A new home for golf prizes.`
- **Lede:** `Greenside will run prize competitions for golf kit, tech and days on the course — with capped entries and full rules published before anything opens.`
- **CTA button:** `Join the waitlist` (anchors to `#join`).
- **CTA note (mono):** `Free to join · Unsubscribe anytime`

### 3.2 Layout

**Mobile.** The hero follows the header and is 602px tall as built.
1. Contour illustration, 390 × 210, full bleed.
2. Copy block: padding 8 top, 44 bottom, 24 sides; gap 20. Contents in order:
   - H1: Display/XL, white.
   - Lede: Body/L, `on-dark-muted`.
   - CTA group (padding-top 4, gap 12): full-width button, then the mono note.

**Desktop.** The hero is 656px tall as built.
- A single row: padding 96 top, 120 bottom, 160 sides; gap 64; items
  vertically centred.
- **Left column, fixed 560 wide, gap 28:**
  - Eyebrow (mono).
  - H1: Display/Desktop XL, white.
  - Lede: Body/L at 19px, `on-dark-muted`.
  - CTA row (gap 20, padding-top 8): a 220 × 52 button, then the mono note.
- **Right column** fills the remaining width (about 496): contour
  illustration 496 × 440.

### 3.3 Mobile first viewport (390 × 844)

**Must stay visible without scrolling:**
- header, contour illustration and H1;
- the full lede;
- the "Join the waitlist" button and the "Free to join · Unsubscribe anytime" note.

As built, the hero ends at y = 666. The signup section's label and heading
also show above the fold.

This answers what Greenside is (H1 and lede), why it exists (capped entries,
full rules published first) and what to do next (the button).

The full form is deliberately **not** forced above the fold. Do not add
elements above the H1 or make the illustration taller than 210px on mobile.

### 3.4 Contour illustration

The key visual idea is a **putting green drawn as in a yardage book**: an
irregular green outline with inner contour lines, a dashed collar, the hole,
and a dashed line of putt from a ball position. No flags, balls-on-tees, clubs
or other clip-art. Implement it as **inline SVG**: strokes only, no fills, no
gradients.

**Shapes** (R = base radius; RY = 0.59 × R; k = R / 190):

| Element | Stroke | Size / notes |
|---|---|---|
| Collar | `paper` at 22% opacity, dashed 2/4 | ring scale 1.12 |
| Green edge | `paper` at 55%, **1.5px** | ring scale 1.00 |
| Contour 1–4 | `paper` at 26%, 26%, 30%, 34% | ring scales 0.80, 0.62, 0.45, 0.29 |
| Hole | white, 1.5px outline | 9px circle (mobile), 10px (desktop) |
| Line of putt | white at 75%, 1.25px, dashed 3/5 | cubic Bézier, ball → hole |
| Ball position | white at 90%, filled | 5px (mobile) or 6px (desktop) dot |

All strokes are 1px unless stated.

**Ring geometry:**
- Each ring is a smooth closed loop through 10 points (a Catmull-Rom curve,
  converted to cubic Béziers).
- Radius factors: `[1.00, 0.86, 1.08, 0.94, 0.80, 0.98, 1.12, 0.90, 0.84, 1.04]`.
- Ring *i* rotates the factor list by *i* and starts at angle `0.35 + 0.12·i`
  radians.
- As rings shrink, their centres drift right and up:
  `cx + (1 − s)·R·0.32`, `cy − (1 − s)·R·0.115`.

**Hole, ball and putt positions:**
- Hole: at the innermost ring's centre, offset by (−10k, −6k).
- Ball: at `(cx − 0.72R, cy + 0.62RY)`.
- Putt control points: `(ball.x + 0.3R, ball.y)` and
  `(hole.x − 0.25R, hole.y + 0.45RY)`.

**Annotations** (Data/Mono in `paper` at 80%; Label/Mono in `on-dark-muted`):

| | Mobile hero | Desktop hero |
|---|---|---|
| "Green 00 · Pre-launch" | (24, 18) | — (eyebrow sits in the copy column) |
| "→ 2.0%" | top-right, x = 390 − 24 − width, y = 40 | top-right corner, y = 36 |
| "↓ 1.5%" | (24, 176) | (0, 330) |
| "Not to scale" | bottom-right, 24 inset, y = h − 28 | bottom-right |

The annotations sit outside the collar so they don't overlap lines.

**Instances:**

| Where | Box | R | Centre (cx, cy) | Extras |
|---|---|---|---|---|
| Mobile hero | 390 × 210 | 160 | 226, 112 | putt, ball, annotations |
| Desktop hero | 496 × 440 | 230 | 268, 226 | putt, ball, annotations |
| Mobile final CTA | 390 × 150 | 150 | 250, 92 | all opacities × 0.7; no putt, ball or annotations |
| Desktop final CTA | 496 × 300 | 200 | 250, 150 | same as mobile final CTA |

The illustration is decorative: `aria-hidden="true"`.

Reference generator (the same maths the Figma frames used; generate the SVG
paths from it, don't hand-draw them):

```js
const F = [1.00,0.86,1.08,0.94,0.80,0.98,1.12,0.90,0.84,1.04];
function ring(cx, cy, rx, ry, i, phase) {
  const n = F.length, p = [];
  for (let j = 0; j < n; j++) {
    const a = (j / n) * 2 * Math.PI + phase, r = F[(j + i) % n];
    p.push([cx + Math.cos(a) * rx * r, cy + Math.sin(a) * ry * r]);
  }
  let d = `M ${p[0][0]} ${p[0][1]}`;
  for (let j = 0; j < n; j++) {
    const p0 = p[(j - 1 + n) % n], p1 = p[j], p2 = p[(j + 1) % n], p3 = p[(j + 2) % n];
    d += ` C ${p1[0] + (p2[0] - p0[0]) / 6} ${p1[1] + (p2[1] - p0[1]) / 6}` +
         ` ${p2[0] - (p3[0] - p1[0]) / 6} ${p2[1] - (p3[1] - p1[1]) / 6} ${p2[0]} ${p2[1]}`;
  }
  return d + ' Z';
}
// rings: s = [1.12, 1.00, 0.80, 0.62, 0.45, 0.29], ring index i = 0..5
// centre_i = (cx + (1-s)*R*0.32, cy - (1-s)*R*0.115); rx = R*s; ry = 0.59*R*s
```

---

## 4. Waitlist form

### 4.1 Components

| Part | Spec |
|---|---|
| Email field | 52px tall; white fill; 2px radius; 16px horizontal padding; no border on dark backgrounds. Placeholder `Email address` in Body/M, `ink-muted`. Typed text in `ink`. It needs a real `<label>` (visually hidden) reading "Email address". |
| Consent checkbox | 18 × 18; 1.5px `on-dark-muted` stroke; 2px radius; gap 10 to the label. Label (Body/S, `on-dark-muted`): `I agree to the Privacy Notice and Terms & Conditions.` "Privacy Notice" and "Terms & Conditions" link to the inline legal panels (§5.6). |
| Button | `action` fill; label `Join the waitlist` in UI/Button, white; 52px tall; 2px radius; 24px horizontal padding. **Full width on mobile.** On desktop: fixed 220 wide in the hero and signup band, 200 in the final CTA. |
| Privacy line | Body/S, `on-dark-muted`: `How we use your email is set out in our Privacy Notice.` |

### 4.2 Arrangement

**Mobile.** The signup section is 508px tall as built.
- Section: `ink` fill, `id="join"`, padding 48 vertical and 24 sides, gap 16.
- Contents in order:
  - mono label `Join the waitlist`;
  - heading `Be first to hear when we open.` (Display/L at 30, white);
  - body `We’ll email you when Greenside launches. You can unsubscribe at any time.` (Body/M, `on-dark-muted`);
  - form (padding-top 8, gap 14): **email → consent → button → privacy line**.

**Desktop.** The signup band is 318px tall as built.
- A single row: `ink` fill, padding 72 vertical and 160 sides, gap 80.
- **Left column, fixed 440, gap 16:** the same label, heading (Display/Desktop
  L at 36) and body copy.
- **Right column** fills the rest (padding-top 36, gap 14), in order:
  - a row with the email field (fills) and a 220px button, gap 12;
  - the consent checkbox;
  - the privacy line.

### 4.3 States (Figma `11:2`, shown on the mobile signup)

**Default**
- Empty field and unticked consent.
- The button is **always enabled**. Validation runs on submit.

**Validation / error** (shown after submit)
- The field keeps what was typed (example `golfer@example`) and gets a **2px
  `error-on-dark` border**.
- Directly under the field: `Enter an email address like name@example.com.`
- The checkbox stroke becomes 2px `error-on-dark`. Directly under it:
  `Tick the box to agree before joining.`
- Each message is a row, gap 8: a "!" glyph (Inter Semi Bold 14) plus the
  message in Body/S, both `error-on-dark`. Colour is never the only signal:
  the glyph and the wording carry the meaning too.
- Messages are linked to their fields (`aria-describedby`); the field gets
  `aria-invalid="true"`.

**Success**
- Replaces the heading and form in place. No redirect. Only the mono label stays.
- Block (gap 14, padding 8 top and bottom):
  1. a 40px tick circle: 1.5px `paper` outline at 70%, containing a 2px white check;
  2. `You’re on the list.` (Display/L at 30, white);
  3. `Thanks for joining. We’ll email you when Greenside launches. You can unsubscribe at any time.` (Body/M, `on-dark-muted`);
  4. `Waitlist · Joined` (mono).
- Use `role="status"`.
- It deliberately **does not promise an immediate welcome email**: the
  Klaviyo sending domain is not configured yet.

---

## 5. Page sections

Order on both sizes:

1. Header
2. Hero
3. Waitlist signup
4. What Greenside is
5. Who's behind it
6. Final signup
7. Privacy & terms
8. Footer

Backgrounds, in the same order:

| Section | Background |
|---|---|
| Header, hero | green |
| Signup | ink |
| What Greenside is | paper |
| Who's behind it | paper, with a 1px `rule-on-paper` top rule |
| Final signup | green |
| Privacy & terms | paper |
| Footer | ink |

Built heights (px):

| Section | Mobile | Desktop |
|---|---|---|
| Header | 64 | 75 |
| Hero | 602 | 656 |
| Signup | 508 | 318 |
| What Greenside is | 925 | 716 |
| Who's behind it | 767 | 671 |
| Final signup | 530 | 508 |
| Privacy & terms | 285 | 341 |
| Footer | 169 | 165 |
| **Total** | **3850** | **3450** |

### 5.1 What Greenside is

**Copy:**
- **Label:** `01 — What Greenside is`
- **Heading:** `Golf prizes, set out plainly.`
- **Body:** `A new home for golf prize competitions — built around the kit, tech and experiences golfers actually want, and around rules you can read before you enter.`

**Three numbered notes** (number `01`–`03` in Data/Mono; title; description):

| # | Title | Description |
|---|---|---|
| 01 | Real golf kit | Equipment, tech and days out that golfers actually want to win. |
| 02 | Capped entries | Every competition will show its maximum number of entries up front. |
| 03 | Clear rules | Full competition rules published before anything opens. |

**Mobile:**
- Section padding: 72 vertical, 24 sides; gap 24.
- Heading: Display/L at 30, `ink`. Body: Body/M, `ink-muted`.
- Notes are a list: each row has a 1px `rule-on-paper` top rule (and a bottom
  rule on the last), padding 16 vertical, gap 20 between number and text.
- In each note, title and description are stacked with gap 4. Title: Inter Semi
  Bold 17, `ink`. Description: Body/S, `ink-muted`.

**Desktop:**
- Section padding: 120 vertical, 160 sides; gap 56.
- Intro row, gap 80:
  - left 440: label and heading (Display/Desktop L 44);
  - right: body in Body/L, `ink-muted`, padding-top 34.
- Notes are **3 equal columns**, gap 32. Each column has a **1px `ink` top
  rule**, padding-top 20 and gap 10. Title: Inter Semi Bold 20. Description:
  Body/M.

### 5.2 Planned prize categories (inside §5.1)

- **Label (mono):** `Planned prize categories`
- **Line (Data/Mono, `ink`, 15 mobile / 16 desktop):** `Equipment · Tech · Experiences`
- **Note (Body/S, `ink-muted`):** `Examples only. No specific prizes are confirmed yet.`

### 5.3 What you won't find (inside §5.1)

- **Label (mono):** `What you won’t find`
- **Line (`ink`; Body/M mobile, Body/L desktop):** `Countdown clocks, spin-to-win or invented urgency.`
- **Approval pending:** this line is a public commitment about how the site
  will behave. Remove it if Ben does not approve it.

**Layout for §5.2–5.3:**
- **Mobile:** stacked under the notes (gap 20, padding-top 8), each group gap 6.
- **Desktop:** a 2-column row, gap 32. Each column has a 1px `rule-on-paper`
  top rule, padding-top 20 and gap 8.

### 5.4 Who's behind it

**Copy:**
- **Label:** `02 — Who’s behind it`
- **Heading:** `Who’s behind Greenside`
- **Body:** `A prize site should be easy to check. These details will be published in full before launch.`
- **Placeholder warning** (Label/Mono in `error-on-paper`):
  `Bracketed items are placeholders — not for publication as shown.` This is
  a **design-review note only. Never ship it.**

**Founder block:**
- Photo placeholder: 112 × 140 on mobile, 176 × 220 on desktop (a 4:5
  portrait). `rule-on-paper` fill; 1px dashed (4/4) `ink-muted` outline at
  60%; 2px radius; label `[Founder photo]`.
- Beside the photo:
  - `[Founder name]`: Inter Semi Bold, 17 mobile / 20 desktop.
  - `[Role]`: mono.
  - `[A few sentences in the founder’s own words on why Greenside exists — to be supplied.]`:
    Body/S on mobile, Body/M on desktop.

**Company details** (label in mono; value in Data/Mono, `ink`, 14 mobile / 15 desktop):

| Label | Value |
|---|---|
| Company | `[Registered company name]` |
| Company no. | `[To add]` |
| Registered address | `[To add]` |
| Contact | `[Contact email]` |

**Mobile:**
- Section padding: 64 top, 72 bottom, 24 sides; gap 24.
- Founder row: photo plus text column, gap 16.
- Details: a stacked list with 1px top rules (bottom rule on the last) and
  padding 12.

**Desktop:**
- A single row: padding 112 top, 120 bottom, 160 sides; gap 80.
- **Left 440:** label, heading (44), body (Body/L), placeholder note.
- **Right column** (padding-top 34, gap 40):
  - founder row: photo and text, gap 28;
  - details as a **2 × 2 grid**, gap 32. Each cell has a top rule and padding 14.

### 5.5 Final signup

**Copy:**
- **Label:** `Waitlist open`
- **Heading:** `When we open, you’ll hear first.`
- **Form:** email field, consent, button `Join the waitlist`, then mono
  `Free to join · Unsubscribe anytime`.

**Mobile:**
- A 390 × 150 contour band at the top (the dimmed final-CTA instance).
- Copy block: padding 0 top, 56 bottom, 24 sides; gap 16.
- Heading: Display/L at 30, white.
- Form: padding-top 8, gap 14, order **email → consent → button → note**.

**Desktop:**
- A single row: padding 104 vertical, 160 sides; gap 64; items vertically centred.
- **Left 560, gap 18:** label and heading (44). Below them the form (padding-top
  12, gap 14):
  - a row with the email field (fills) and a 200px button, gap 12;
  - the consent checkbox;
  - the note.
- **Right:** contour drawing, 496 × 300.

### 5.6 Privacy & terms

**Copy:**
- **Label:** `Privacy & terms`
- **Two accordion rows:** `Privacy Notice` and `Terms & Conditions`, each with
  a `+` (Data/Mono) on the right.
- **Note (Body/S, `ink-muted`):** `Shown in full on this page while the site is in pre-launch.`

**Layout:**
- **Mobile:**
  - Padding 40 vertical, 24 sides.
  - Rows: padding 16; titles Inter Semi Bold 16; "+" at 18.
  - Rows have 1px `rule-on-paper` top rules, with a bottom rule on the last.
  - 16px gap after the label; 12px gap before the note.
- **Desktop:** padding 64 vertical, 160 sides; rows padding 20; titles 18; "+" at 20.

**Behaviour:**
- Each row is a native `<details>`/`<summary>`. The expanded state was not
  designed; **Proposed:** the body text is Body/M `ink` with a max width of
  720, and "+" becomes "−".
- The content is the **existing published Shopify pages** `privacy-notice`
  and `terms-conditions`, rendered inline with the wording unchanged, because
  the storefront password blocks `/pages/*`.
- Footer and consent links open the matching panel (`#privacy-notice`,
  `#terms-conditions`).

### 5.7 Footer

- **Links (Body/S, `paper`, underlined):** `Privacy Notice` · `Terms & Conditions` · `Contact`
- **Legal line (Label/Mono, `on-dark-muted`):** `© 2026 Greenside Competitions · Company no. [to add] · [Registered address]`

**Mobile:**
- Padding 32 vertical, 24 sides; gap 16.
- Stacked: wordmark (14px), links row (gap 20), legal line.

**Desktop:**
- Padding 40 vertical, 160 sides; gap 24.
- Row 1 (`space-between`): wordmark (16px) and links (gap 28).
- Row 2: a 1px `rule-on-dark` top rule with padding-top 20, then the legal line.

---

## 6. Trust placeholders (must be replaced before publication)

None of these values exist yet. **Do not invent them.** Ben must supply each
one, and the page must not go live with bracketed text.

| # | Placeholder | Appears in |
|---|---|---|
| 1 | `[Registered company name]` | Who's behind it: company details |
| 2 | Company number: `[To add]` / `[to add]` | Who's behind it; footer legal line |
| 3 | Registered address: `[To add]` / `[Registered address]` | Who's behind it; footer legal line |
| 4 | `[Contact email]` | Who's behind it; footer "Contact" link target |
| 5 | `[Founder name]` | Who's behind it |
| 6 | `[Role]` | Who's behind it |
| 7 | `[Founder photo]` | Who's behind it (genuine photo, 4:5 portrait) |
| 8 | `[A few sentences … to be supplied.]` founder statement | Who's behind it |
| 9 | "Greenside Competitions" in the footer © line | To confirm: trading name versus the registered entity name |
| 10 | The placeholder warning note | Review-only; remove before publishing |

Approvals still needed alongside the placeholders:

- the "What you won’t find" line (§5.3);
- that the Privacy Notice and Terms & Conditions content is current.

---

## 7. Shopify implementation notes

### 7.1 Where it lives

- The page is the **storefront password page**. `templates/password.json`
  (layout `password`) points to one new section; the storefront password stays on.
- Work on an **unpublished staging theme** and verify each upload by
  `checksumMd5` against a local `md5sum`.
- The agent cannot publish, and cannot write to the live MAIN theme
  (`198832161142`). Ben publishes from the admin.
- Horizon's `layout/password.liquid` appends the `password-footer` section
  ("Powered by Shopify", "Enter using password", store-owner link) below the
  page. **Keep it**, because staff need it to get in, but check how it sits
  under the new footer.

### 7.2 Component → Shopify mapping (Proposed)

| Figma component | Shopify file |
|---|---|
| Whole page (sections 1–8) | `sections/greenside-prelaunch.liquid`, one section with schema settings for all editable copy, so copy changes need no code change. Replaces the superseded `greenside-landing.liquid` (§8) |
| Contour illustration | `snippets/greenside-contours.liquid`: inline SVG with parameters `variant: 'hero' / 'final'` and `size: 'mobile' / 'desktop'`, and paths precomputed from the §3.4 generator. Rendered twice; mobile and desktop instances swapped by CSS media query |
| Signup form (3 instances: hero CTA is a link; signup band and final CTA are forms) | `snippets/greenside-signup-form.liquid`, taking a `form_id` and a `layout: 'stacked' / 'inline'` parameter |
| Form states | Liquid `{% form 'customer' %}` with hidden `contact[tags]=newsletter`. **Default:** render fields. **Error:** `form.errors` → field messages and `aria-invalid`. **Success:** `form.posted_successfully?` → success block |
| Consent checkbox | `required` checkbox, client-side only; it is not sent to Shopify. Its error message ("Tick the box to agree before joining.") needs a few lines of JS on submit, because the browser's default bubble is not the designed message. Without JS the browser's native validation still blocks the submit |
| Legal panels | `pages['privacy-notice'].content` and `pages['terms-conditions'].content`, rendered in `<details>` with `h1` demoted to `h2` (as the superseded section already does) |
| Wordmark | Live text in HTML/CSS: "GREENSIDE", Bricolage Grotesque SemiBold, `letter-spacing: 0.14em`. No image |
| Fonts | Self-host WOFF2 files of Bricolage Grotesque (600, 700), Inter (400, 600) and IBM Plex Mono (400, 500) in `assets/`, with `@font-face` and `font-display: swap`; preload the H1 font. **To verify** whether any of these are in Shopify's font library first. Do not load Google Fonts from a CDN |
| Colour and spacing tokens | CSS custom properties scoped to the section root, named after the Figma variables (`--gs-ink`, `--gs-green`, …) |
| Share image | `assets/greenside-share.png` from commit `3952c70` (1200 × 630, made from the old logo). Set it manually in theme settings if wanted; a new one matching Direction A is **Proposed**, not designed |

### 7.3 Responsive behaviour

- Build mobile-first. The desktop two-column rows (hero, signup band, What
  intro, Who, final CTA) collapse to a single column below the desktop
  breakpoint (§1.4). The 3-column notes and the 2 × 2 details grid become
  stacked lists.
- Buttons are full width on mobile and fixed width (220 or 200) on desktop.
- Keep the mobile first-viewport rules in §3.3.

### 7.4 Things to verify during implementation

- **Two forms on one page.** Shopify's `form.posted_successfully?` is true for
  every `customer` form on the page after one submits, so both could show
  success. Decide whether that is acceptable, or scope the success state to
  the submitted form (for example with a return anchor).
- **Duplicate emails.** What Shopify returns when an existing subscriber signs
  up again.
- **Klaviyo sync.** Whether Shopify's email subscribers actually reach Klaviyo
  `Email List` (`Uh2nHm`). The 25 Sep audit found this **inconsistent**, and
  it must be confirmed in the Klaviyo UI before publishing. The Welcome flow
  (`TZTeRu`) cannot deliver reliably until a sending domain is authenticated.
- **One H1 only.** Legal page content must be demoted to `h2`.
- **Accessibility.** The illustration is `aria-hidden`; the email field has a
  real label; errors are announced; the success block uses `role="status"`.

---

## 8. Explicit exclusions and superseded work

The approved design does **not** use, and the implementation must not add:

- countdown timers or closing-soon urgency;
- fake scarcity ("only X left", "selling fast");
- fake or invented social proof (subscriber counts, testimonials, winners);
- gambling-style UI: progress bars, filling meters, spin/instant-win styling,
  confetti;
- the claim **"Every draw filmed"**, which is held back as a future claim
  pending legal approval;
- invented company, founder or contact details;
- unapproved prize claims (specific prizes, brands, prize values, odds);
- brass/gold (reserved for future winner contexts);
- the illustrated golf-ball logo as the page's primary mark;
- gradients, shadows or rounded cards.

**Superseded, not the design:**

- **Staging theme `200108802422`** ("Greenside Landing (Staging)", unpublished)
  holds the **old landing implementation**: `sections/greenside-landing.liquid`,
  `templates/index.landing-preview.json` and `assets/greenside-share.png` from
  commit `3952c70` on branch `claude/greenside-allocator-qa-verify-f9at5v`. It
  was reviewed on 25 Sep 2026 and judged too generic. **Do not treat it as the
  final design, and do not publish it.** Its `password.json` was never changed.
- **Figma page `03 Directions`** (section `6:11`) holds the three superseded
  explorations. They still contain "Every draw filmed" and an **incorrect
  "Greenside Competitions Ltd"** footer line that was never verified. Only
  `04 Pre-launch page` is the approved design.

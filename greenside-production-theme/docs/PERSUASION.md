# Persuasion, and where the line is

Greenside uses behavioural principles on purpose. Scarcity, urgency, anchoring
and loss aversion all appear in the storefront. What separates them from the
tactics this brand is defined against is not restraint in the wording — it is
whether the underlying fact is real, published in advance, and verifiable by the
entrant after the fact.

This document records why each mechanism is built the way it is. It exists
because every one of these is one small edit away from becoming a dark pattern,
and the edit usually looks like an improvement at the time.

`tests/persuasion-integrity.test.js` enforces most of what follows. If you
change something here, that file should fail first.

---

## The rule

> Every persuasive element must be traceable to a fact the business has already
> committed to, and must survive the entrant checking it afterwards.

A number that cannot be checked is a claim. A claim that turns out to be wrong
is the end of a competition brand.

---

## Scarcity — the entry cap

**What is shown:** `2,570 of 4,000 entries`, `1,430 entries left`.

**Why it is legitimate:** the cap is a real, finite allocation published before
anyone enters. Running out is a genuine consequence, not a manufactured one.

**How it is protected:**

- `snippets/competition-progress.liquid` renders only when **both**
  `custom.entries_total` and `custom.entries_sold` hold real values and the
  total is above zero. There is deliberately no fallback. A competition with no
  entry data shows no progress at all, rather than an invented bar.
- Neither metafield may be given a `| default:`. A defaulted cap is a fabricated
  cap. A test asserts this.
- The bar is one neutral colour at every percentage. A fill that turns gold as
  it approaches full is the most recognisable cue in online gambling, and the
  number already carries the information.
- Cards do not show the progress bar (`card_show_progress` is off). Twelve
  filling bars in a grid is a slot machine, whatever each individual bar says.

**Never do:** show a percentage without the underlying counts; animate the bar
on load; add "almost gone" wording above a threshold.

---

## Urgency and loss aversion — the closing date

**What is shown:** `Closes Wed 23 Sep at 8:00 pm · in 5 days`, and on the day,
`Closing today at 8:00 pm`.

**Why it is legitimate:** the deadline is real and already published. Stating it
precisely is information, not pressure. A customer who has to open a calendar to
work out whether "Wed 23 Sep" is urgent has been given a deadline that cannot
inform their decision.

**How it is protected:**

- The label is derived entirely from `custom.closing_at`. No date, no output.
- `detail: true` (the competition page) adds the time and the day count. Cards
  and basket lines use the short form, so a grid is not a wall of deadlines.
- **Midnight is treated as "no time stated".** Shopify stores `00:00` when a
  merchant sets a date without a time. Rendering that as "at 12:00 am" would
  present a storage artefact as a stated cut-off the business never committed
  to. `has_time` suppresses it.
- Day counts are whole calendar days, computed from midnight to midnight, so a
  deadline late tomorrow does not round down to zero days.

**Never do:** add a live countdown timer. A ticking clock converts a real
deadline into manufactured pressure and is the single clearest signal that a
site is optimising against the customer. A test greps for this.

---

## Anchoring — prize value beside entry price

**What is shown:** `£529 prize · £2.49 an entry`.

**Why it is legitimate:** both figures are already published on the page, in
separate blocks. The customer was being asked to hold them in their head and
compare. The anchor puts them next to each other and draws no conclusion.

**How it is protected:**

- `snippets/competition-anchor.liquid` renders only when a real
  `custom.prize_value` exists and the product has a price.
- The copy may not contain a multiplier, a saving, or the word "worth". Framing
  an entry as "worth 200× what you pay" invites the reader to treat it as an
  investment with an expected return. It is not one: the expected return on a
  competition entry is negative, which is true of every competition ever run and
  precisely why that framing is dishonest. A test asserts the copy stays factual.
- Entry bundles are priced at exact multiples. Where a bundle genuinely is
  cheaper per entry, `entry-selector.liquid` marks it — and only then.

**Never do:** show a struck-through "was" price for an entry; describe a bundle
as a saving when it is the same per-entry price.

---

## Odds — off by default, and why

**What is shown, when enabled:** `1 in 4,000 — at the full entry cap`.

This is the strongest honest trust signal available to a capped competition, and
almost no competitor publishes it. It is also the easiest thing on the site to
get subtly wrong, so it ships **off** (`settings.show_odds`).

**Calculated against the cap, never the sold count.** With 2,570 of 4,000 taken,
dividing by the sold count would render one entry as `1 in 2,570` instead of
`1 in 4,000`. That flatters the number and drifts upward as the competition
fills. The cap is fixed, published in advance, and produces the **worst case**
for the entrant: if the draw is made from sold entries only, a real chance can
only be better than the figure shown. Understating the customer's chance is the
only safe direction to be wrong in. A test asserts `entries_sold` is not
referenced in the odds snippet.

**Before switching it on**, the competition rules must state that the draw is
made from the full set of issued entry numbers. The wording "at the full entry
cap" is an assertion about how your draw works, and the storefront must not make
it before the rules do.

Where a customer holds more entries than the cap — a data error — nothing is
shown, rather than claiming a certainty of winning.

---

## Social proof — what is missing, and why nothing fills it

There is no fabricated social proof anywhere, and there is currently very little
real social proof, because the business has not yet run a draw.

**Built and waiting for real data:**

- `winner` metaobject and `sections/winners-rail.liquid` — currently 0 entries,
  showing an honest empty state: *"Winners appear here after the first draw."*
- `faq` metaobject and `sections/faq.liquid` — currently 0 entries.

**Explicitly banned**, enforced by test: "X people viewing", "someone just
entered", "N joined in the last hour", "selling fast", "almost gone", "hurry".
Star ratings and review counts are emitted in structured data only from a
metafield holding real values, never from a fallback.

The honest empty state is the pattern to follow. It explains the mechanism and
builds trust, where an invented number spends it.

---

## Post-purchase — promises the business can keep

**What is shown:** a "What happens after you enter" block on the competition
page, listing the confirmation email, the closing date, the draw date, and that
the draw is published in full.

Each line is conditional on data that exists. The draw date appears only when
`custom.draw_at` is set.

**The entry-numbers lesson.** The basket previously read: *"Entry numbers are
emailed the moment your payment clears, and stay on your order in your
account."* Both halves were false. Nothing in the stack allocates entry numbers,
and with new customer accounts the hosted portal does not display them. That
copy would have broken on the first real order — the worst possible moment, for
the customer who had just paid.

Entry-number copy now sits behind `settings.entry_numbers_issued`, off by
default. **Turn it on only once something actually allocates numbers and sends
them** — Shopify Flow or a small app. A test asserts no ungated entry-number
promise exists in the copy.

The general rule: if the storefront says a customer will receive something,
something must send it.

---

## Consent

No consent checkbox is pre-ticked anywhere. A pre-ticked marketing box is
unlawful under UK GDPR and is the most common consent dark pattern in ecommerce.
A test scans every checkbox input for a `checked` attribute alongside
consent-related wording.

The Club signup states "Free to join. Unsubscribe any time." because both are
true, and every email must carry a working unsubscribe link.

---

## Number formatting

Entry counts pass through `snippets/format-number.liquid`, which groups
thousands: `2,570 of 4,000`, not `2570 of 4000`. This is not cosmetic. The cap is
the basis of the entire scarcity claim, and an unformatted four-digit number
reads as careless where a grouped one reads as counted. The live updater in
`product.js` already used `toLocaleString()`, so the server render was the
inconsistent half.

---

## What to check before adding anything persuasive

1. What fact is this based on, and where is it stored?
2. What happens when that fact is missing — does it disappear, or invent a value?
3. Can the entrant verify it after the draw?
4. If it is a promise, what sends it?
5. Does it still read as honest to someone who did not win?

If any answer is uncomfortable, the element does not ship.

/**
 * Persuasion integrity.
 *
 * Greenside uses behavioural principles deliberately — genuine scarcity, a real
 * deadline, an explicit price anchor — and the line between those and a dark
 * pattern is whether the underlying fact is real and verifiable.
 *
 * These tests encode that line. They are not style checks: each one corresponds
 * to a claim the storefront would be making falsely if it failed.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const stripComments = (s) =>
  s
    .replace(/\{%-?\s*comment\s*-?%\}[\s\S]*?\{%-?\s*endcomment\s*-?%\}/g, '')
    .replace(/\{%-?\s*#[\s\S]*?-?%\}/g, '');

const readMarkup = (p) => stripComments(read(p));

const liquidFiles = () => {
  const out = [];
  const walk = (rel) => {
    const full = path.join(ROOT, rel);
    if (!fs.existsSync(full)) return;
    for (const entry of fs.readdirSync(full, { withFileTypes: true })) {
      const next = path.join(rel, entry.name);
      if (entry.isDirectory()) walk(next);
      else if (entry.name.endsWith('.liquid')) out.push(next);
    }
  };
  ['snippets', 'sections', 'layout', 'templates', 'blocks'].forEach(walk);
  return out;
};

const settings = JSON.parse(read('config/settings_data.json')).current;
const copy = JSON.parse(read('locales/en.default.json'));

/* -------------------------------------------------------------------------- *
 * No countdown timers
 * -------------------------------------------------------------------------- */

// A ticking clock converts a real deadline into manufactured pressure, and is
// the visual signature of the competition sites this brand is defined against.
// Dates and day counts are fine; a live-updating timer is not.
test('no countdown timer runs anywhere in the theme', () => {
  const offenders = [];
  for (const file of ['assets/theme.js', 'assets/product.js', 'assets/cart.js', 'assets/header.js']) {
    const full = path.join(ROOT, file);
    if (!fs.existsSync(full)) continue;
    const body = read(file);
    if (/setInterval\s*\([^)]*\b(countdown|remaining|timeLeft|deadline)\b/i.test(body)) {
      offenders.push(`${file}: interval-driven countdown`);
    }
    if (/\bcountdown\b/i.test(body)) offenders.push(`${file}: references a countdown`);
  }
  assert.deepEqual(offenders, [], `Countdown timers are a dark pattern here:\n${offenders.join('\n')}`);
});

/* -------------------------------------------------------------------------- *
 * Scarcity must come from real data
 * -------------------------------------------------------------------------- */

// Every scarcity figure has to trace back to a metafield. A default or fallback
// would mean inventing a number, and an invented cap is the whole fraud.
test('entry counts render only from real metafield values', () => {
  const body = readMarkup('snippets/competition-progress.liquid');
  assert.match(body, /entries_total/, 'progress must read the published cap');
  assert.match(body, /entries_sold/, 'progress must read the sold count');
  assert.doesNotMatch(
    body,
    /entries_total[^\n]*\|\s*default:/,
    'the entry cap must never fall back to an invented default',
  );
  assert.doesNotMatch(
    body,
    /entries_sold[^\n]*\|\s*default:/,
    'the sold count must never fall back to an invented default',
  );
});

/* -------------------------------------------------------------------------- *
 * Odds
 * -------------------------------------------------------------------------- */

// Quoting odds against entries sold flatters the figure and drifts upward as a
// competition fills. The published cap is fixed and is the worst case for the
// entrant, so a real chance can only be better than the number shown.
test('odds are calculated against the published cap, never the sold count', () => {
  const body = readMarkup('snippets/competition-odds.liquid');
  assert.match(body, /entries_total/, 'odds must divide by the published cap');
  assert.doesNotMatch(body, /entries_sold/, 'odds must not be based on entries sold');
});

// Odds assert how the draw pool is defined. Shipping them on by default would
// publish that claim for every merchant before their rules agree with it.
test('odds are off until switched on deliberately', () => {
  const schema = JSON.parse(read('config/settings_schema.json'));
  const all = schema.flatMap((group) => group.settings || []);
  const odds = all.find((s) => s.id === 'show_odds');

  assert.ok(odds, 'show_odds must exist as a setting');
  assert.equal(odds.default, false, 'show_odds must default to false');
  assert.notEqual(settings.show_odds, true, 'show_odds must not be enabled in shipped settings');

  const body = readMarkup('snippets/competition-odds.liquid');
  assert.match(body, /settings\.show_odds/, 'the snippet must be gated on the setting');
});

/* -------------------------------------------------------------------------- *
 * Promises the business can keep
 * -------------------------------------------------------------------------- */

// Entry numbers were promised in the basket for months while nothing allocated
// them, so the first real order would have broken the promise. Any copy that
// says entrants receive numbers must sit behind the flag that says they do.
test('entry numbers are only promised when something issues them', () => {
  const flagged = readMarkup('snippets/what-happens-next.liquid');
  assert.match(
    flagged,
    /settings\.entry_numbers_issued/,
    'the entry-number line must be gated on the setting',
  );

  const reassurance = String(settings.cart_reassurance || '');
  if (!settings.entry_numbers_issued) {
    assert.doesNotMatch(
      reassurance,
      /entry number/i,
      'cart reassurance must not promise entry numbers while none are issued',
    );
  }

  // The copy file must not carry an ungated promise either.
  const flat = [];
  const walk = (node, prefix = '') => {
    if (typeof node === 'string') flat.push([prefix, node]);
    else if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) walk(v, prefix ? `${prefix}.${k}` : k);
    }
  };
  walk(copy);

  const allowed = new Set(['products.competition.next_numbers']);
  const promises = flat.filter(
    ([key, value]) => /entry number/i.test(value) && !allowed.has(key),
  );
  assert.deepEqual(
    promises.map(([k]) => k),
    [],
    'entry-number copy must live only behind the entry_numbers_issued flag',
  );
});

/* -------------------------------------------------------------------------- *
 * No invented social proof
 * -------------------------------------------------------------------------- */

// Entrant counts, "X people viewing", star ratings and testimonials are the
// standard fabrications in this category. Winners come from the metaobject or
// they do not appear at all.
test('no fabricated social proof anywhere in the theme', () => {
  const banned = [
    /\bpeople are viewing\b/i,
    /\bviewing (this|right) now\b/i,
    /\bsomeone (just )?entered\b/i,
    /\bjoined in the last\b/i,
    /\bhurry\b/i,
    /\balmost gone\b/i,
    /\bselling fast\b/i,
  ];
  const offenders = [];

  for (const file of liquidFiles()) {
    const body = readMarkup(file);
    for (const pattern of banned) {
      if (pattern.test(body)) offenders.push(`${file}: ${pattern}`);
    }
  }

  const flat = [];
  const walk = (node, prefix = '') => {
    if (typeof node === 'string') flat.push([prefix, node]);
    else if (node && typeof node === 'object') {
      for (const [k, v] of Object.entries(node)) walk(v, prefix ? `${prefix}.${k}` : k);
    }
  };
  walk(copy);
  for (const [key, value] of flat) {
    for (const pattern of banned) {
      if (pattern.test(value)) offenders.push(`${key}: "${value}"`);
    }
  }

  assert.deepEqual(offenders, [], `Fabricated urgency or social proof:\n${offenders.join('\n')}`);
});

// A rating or review count must come from a metafield holding real values.
test('ratings and reviews are never emitted without real data', () => {
  const body = readMarkup('snippets/structured-data.liquid');
  const hasRating = /aggregateRating/.test(body);
  if (hasRating) {
    assert.match(
      body,
      /metafields/,
      'aggregateRating may only be emitted from a metafield holding real values',
    );
  }
});

/* -------------------------------------------------------------------------- *
 * Anchoring stays factual
 * -------------------------------------------------------------------------- */

// Framing an entry as "worth 200x what you pay" invites the reader to treat it
// as an investment with an expected return. The anchor states two published
// figures and draws no conclusion from them.
test('the price anchor states facts without implying a return', () => {
  const body = readMarkup('snippets/competition-anchor.liquid');
  assert.match(body, /prize_value/, 'the anchor must use the published prize value');
  assert.doesNotMatch(body, /times|multiplier|worth|save|saving|discount/i,
    'the anchor must not imply a multiple, a saving or a return');

  const anchorCopy = copy.products.competition.anchor;
  assert.doesNotMatch(anchorCopy, /\bx\b|times|worth|save|only/i,
    `anchor copy must not editorialise: "${anchorCopy}"`);
});

/* -------------------------------------------------------------------------- *
 * Deadlines are real
 * -------------------------------------------------------------------------- */

test('closing labels come from the stored date and nothing else', () => {
  const body = readMarkup('snippets/competition-closing.liquid');
  assert.match(body, /closing_at/, 'must read the real closing date');
  assert.match(body, /if stamp != blank/, 'no date must mean no output');
  assert.doesNotMatch(body, /closing_at[^\n]*\|\s*default:/, 'closing date must never be defaulted');
});

// Midnight is what Shopify stores when a merchant sets a date but no time.
// Presenting it as "at 12:00 am" would invent a cut-off they never stated.
test('a midnight timestamp is not presented as a stated cut-off time', () => {
  const body = readMarkup('snippets/competition-closing.liquid');
  assert.match(body, /'0000'/, 'must detect a midnight timestamp');
  assert.match(body, /has_time/, 'must suppress the time when there is none');
});

/* -------------------------------------------------------------------------- *
 * Consent
 * -------------------------------------------------------------------------- */

// A pre-ticked marketing box is unlawful under UK GDPR and is the single most
// common consent dark pattern.
test('no consent checkbox is pre-ticked', () => {
  const offenders = [];
  for (const file of liquidFiles()) {
    const body = readMarkup(file);
    for (const match of body.matchAll(/<input\b[^>]*type=["']checkbox["'][^>]*>/gi)) {
      const tag = match[0];
      if (/\bchecked\b/.test(tag) && /accept|consent|marketing|subscribe|opt/i.test(tag)) {
        offenders.push(`${file}: ${tag.slice(0, 90)}`);
      }
    }
  }
  assert.deepEqual(offenders, [], `Pre-ticked consent:\n${offenders.join('\n')}`);
});

/* -------------------------------------------------------------------------- *
 * Number formatting
 * -------------------------------------------------------------------------- */

// The entry cap is the basis of the scarcity claim. "2570 of 4000" reads as
// careless where "2,570 of 4,000" reads as counted.
test('entry counts are grouped with thousands separators', () => {
  const body = readMarkup('snippets/competition-progress.liquid');
  assert.match(body, /render 'format-number'/, 'counts must pass through the formatter');
  assert.match(body, /sold: sold_text/, 'the visible count must use the formatted value');
  assert.match(body, /total: total_text/, 'the visible total must use the formatted value');
});

/* -------------------------------------------------------------------------- *
 * The competition ticker
 * -------------------------------------------------------------------------- */

// A moving strip is one edit away from being a countdown. What makes this one
// acceptable is that its motion is navigational: it exposes more competitions
// than fit on a line, and implies nothing about time running out.
test('the ticker moves to show more, never to imply time running out', () => {
  const body = readMarkup('sections/competition-ticker.liquid');

  assert.doesNotMatch(body, /\bcountdown\b/i, 'the ticker must never count down');
  assert.doesNotMatch(
    body,
    /\b(hurry|ending soon|last chance|almost gone|don't miss|selling fast)\b/i,
    'the ticker must not carry urgency wording',
  );

  // Every figure in it has to come from the product, not from the section.
  assert.match(body, /product\.price/, 'the price must come from the product');
  assert.match(body, /product\.url/, 'each item must link to its own competition');
  assert.doesNotMatch(
    body,
    /£\s*\d/,
    'no price may be hard-coded into the ticker markup',
  );
});

// A ticker advertising an empty catalogue is worse than no ticker, and an
// unavailable competition in a promotional strip is an advert for a dead end.
test('the ticker renders nothing without real open competitions', () => {
  const body = readMarkup('sections/competition-ticker.liquid');
  assert.match(body, /open_count > 0/, 'the section must render only when items exist');
  assert.match(body, /product\.available/, 'only enterable competitions may appear');
  assert.doesNotMatch(
    body,
    /is_closed/,
    'a passed closing date must not exclude a competition Shopify will still sell',
  );
});

// Motion preferences are an accessibility requirement, and a marquee that
// escapes its container scrolls the whole page sideways.
test('the ticker respects reduced motion and cannot overflow the page', () => {
  const css = read('assets/component-commerce.css');

  assert.match(css, /prefers-reduced-motion/, 'the ticker must honour reduced motion');
  const reduced = css.slice(css.indexOf('prefers-reduced-motion'));
  assert.match(reduced, /animation:\s*none/, 'reduced motion must stop the animation');

  assert.match(css, /\.ticker\s*\{[^}]*overflow:\s*hidden/, '.ticker must clip its own track');
  assert.match(
    css,
    /animation-play-state:\s*paused/,
    'the ticker must pause on hover and focus',
  );
});

/* -------------------------------------------------------------------------- *
 * Availability comes from inventory, and is clamped
 * -------------------------------------------------------------------------- */

// entries_sold is hand-maintained and nothing writes to it, so it is wrong in
// public the moment a real order exists. Inventory is decremented by Shopify
// on every order, which is why it is the customer-facing source of truth.
test('availability is derived from live inventory, never from entries_sold', () => {
  const body = readMarkup('snippets/competition-availability.liquid');

  assert.match(body, /inventory_quantity/, 'availability must read live inventory');
  assert.match(body, /entries_total/, 'availability must read the published cap');
  assert.doesNotMatch(
    body,
    /entries_sold/,
    'availability must not use the hand-maintained entries_sold metafield',
  );
  assert.match(
    body,
    /inventory_management == 'shopify'/,
    'availability must require that Shopify is tracking inventory',
  );
});

// Draft Orders bypass the inventory DENY cap -- two completed against a stock
// of 1 left inventory at -1 on the live store. Postal entries are Draft
// Orders, so negative inventory is a real state and must never surface as
// "-1 entries left".
test('a negative inventory from a postal entry never reaches the customer', () => {
  const body = readMarkup('snippets/competition-availability.liquid');

  assert.match(body, /if remaining < 0/, 'remaining must be clamped at zero');
  assert.match(body, /if remaining > total/, 'remaining must be clamped at the cap');
  assert.match(
    body,
    /sold_out/,
    'a competition at or past its cap must say so rather than quote a number',
  );
  assert.doesNotMatch(
    body,
    /is_closed/,
    'a passed closing date must not suppress a count of entries still for sale',
  );
});

/* -------------------------------------------------------------------------- *
 * A count, not a meter
 * -------------------------------------------------------------------------- */

// The distinction the whole commercial redesign rests on: the number is
// information a customer can check, the filling bar is the visual signature of
// online gambling. Cards get the number and never the bar.
test('cards carry an availability count and never a progress bar', () => {
  assert.notEqual(
    settings.card_show_progress,
    true,
    'the progress bar must stay off on cards',
  );

  const card = readMarkup('snippets/competition-card.liquid');
  assert.match(card, /render 'competition-availability'/, 'the card must show the count');

  const availability = readMarkup('snippets/competition-availability.liquid');
  assert.doesNotMatch(
    availability,
    /progress|<meter|--fill|width:\s*\{\{/,
    'the availability snippet must render text, never a bar or a fill',
  );

  const css = read('assets/component-commerce.css');
  assert.doesNotMatch(
    css,
    /\.competition-availability[^{]*\{[^}]*(background-image|linear-gradient)/,
    'the availability line must not be dressed up as a meter',
  );
});

/* -------------------------------------------------------------------------- *
 * Inventory is the gate, a date is not
 *
 * Shopify enforces stock at checkout; it does not enforce closing_at. Any
 * place the theme decides whether a customer may enter must therefore read
 * product availability. A theme that hid the entry form on a date while
 * Shopify still held stock would tell the customer a competition was closed
 * and then charge anyone who arrived by direct link.
 * -------------------------------------------------------------------------- */

test('no entry gate anywhere is driven by closing_at', () => {
  const gated = [
    'sections/main-competition.liquid',
    'sections/hero-competition.liquid',
    'sections/competition-ticker.liquid',
    'snippets/competition-card.liquid',
    'snippets/competition-availability.liquid',
    'snippets/competition-badges.liquid',
  ];

  for (const file of gated) {
    const body = readMarkup(file);
    assert.doesNotMatch(body, /is_closed/, `${file} must not gate entry on a date`);
    assert.doesNotMatch(
      body,
      /can_enter[^\n]*is_closed/,
      `${file} must derive can_enter from inventory alone`,
    );
  }
});

test('the competition page derives can_enter from product.available alone', () => {
  const body = readMarkup('sections/main-competition.liquid');
  assert.match(body, /assign can_enter = product\.available/, 'inventory is the gate');
  assert.doesNotMatch(body, /closed_title/, 'the date-driven closed state is gone');
});

test('a sold-out competition is still rendered, never hidden', () => {
  // The product page must keep serving a page at zero stock: entrants who
  // already bought need somewhere to return to, and links already sent must
  // not 404.
  const body = readMarkup('sections/main-competition.liquid');
  assert.match(body, /competition-closed/, 'a sold-out state is rendered in place');
  assert.match(body, /sold_out_title/, 'and it says sold out');
});

/* -------------------------------------------------------------------------- *
 * A date that has passed says nothing
 *
 * The live QA-3 test showed a competition past its closing_at, with stock and
 * a working Enter button, rendering "Closed" and "Entries close on Tue 1 Sep".
 * Both told the customer the opposite of what the page would let them do.
 * Entries end when inventory runs out, so a passed date describes nothing.
 * -------------------------------------------------------------------------- */

test('a closing or draw date that has passed renders nothing at all', () => {
  const body = readMarkup('snippets/competition-closing.liquid');

  assert.match(body, /assign is_future = false/, 'the snippet must test whether the date is ahead');
  assert.match(body, /{%- if is_future -%}/, 'and render only when it is');
  assert.doesNotMatch(
    body,
    /competition\.closed/,
    'a passed date must never render the word Closed',
  );
});

test('what-happens-next hides deadlines that have already passed', () => {
  const body = readMarkup('snippets/what-happens-next.liquid');

  assert.match(body, /{%- if closing_is_future -%}/, 'the closing line is gated on a future date');
  assert.match(body, /{%- if draw_is_future -%}/, 'so is the draw line');
  assert.doesNotMatch(
    body,
    /{%- if closing_at != blank -%}/,
    'merely having a date is not enough to state it as a deadline',
  );
});

test('priceValidUntil is never published with a date that has passed', () => {
  // It means "the price is no longer available after this date". A stale value
  // tells search engines the offer expired while the competition is on sale.
  const body = readMarkup('snippets/structured-data.liquid');
  assert.match(body, /{%- if closing_is_future -%},/, 'guarded on a future date');
  assert.match(body, /priceValidUntil/, 'and still emitted when the date is ahead');
});

test('no competition surface can claim closed from a date', () => {
  // Sweep: the only thing allowed to produce a closed/sold-out claim is stock.
  const surfaces = [
    'sections/main-competition.liquid',
    'sections/hero-competition.liquid',
    'sections/competition-ticker.liquid',
    'snippets/competition-card.liquid',
    'snippets/competition-badges.liquid',
    'snippets/competition-availability.liquid',
    'snippets/competition-closing.liquid',
    'snippets/what-happens-next.liquid',
  ];
  for (const file of surfaces) {
    const body = readMarkup(file);
    assert.doesNotMatch(body, /is_closed/, `${file} must not compute a date-driven closed state`);
    assert.doesNotMatch(
      body,
      /competition\.badge_closed|competition\.closed_title|competition\.closed'/,
      `${file} must not render date-driven closed copy`,
    );
  }
});

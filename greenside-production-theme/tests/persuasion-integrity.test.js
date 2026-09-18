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

/**
 * The design system's rules, enforced.
 *
 * These are not style preferences. Each one was a specific finding in the brand
 * audit, and each is the kind of thing that creeps back in one component at a
 * time until the site looks like a competition site again.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const stripLiquidComments = (s) =>
  s
    .replace(/\{%-?\s*comment\s*-?%\}[\s\S]*?\{%-?\s*endcomment\s*-?%\}/g, '')
    .replace(/\{%-?\s*#[\s\S]*?-?%\}/g, '');

const cssFiles = () =>
  fs
    .readdirSync(path.join(ROOT, 'assets'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => path.join('assets', f));

const liquidFiles = () => {
  const out = [];
  for (const dir of ['sections', 'snippets', 'blocks', 'layout']) {
    for (const f of fs.readdirSync(path.join(ROOT, dir))) {
      if (f.endsWith('.liquid')) out.push(path.join(dir, f));
    }
  }
  return out;
};

/* -------------------------------------------------------------------------- *
 * Brass means winners
 * -------------------------------------------------------------------------- */

test('brass is never used for progress, urgency or calls to action', () => {
  // A gold meter filling up is the most recognisable cue in online gambling.
  // Brass on this site means winners and achievement, and nothing else.
  const forbidden = [
    'progress__fill',
    'progress__track',
    'badge--closing',
    'badge--urgent',
    'button--primary',
    'competition-card__cta'
  ];

  const offenders = [];
  for (const file of cssFiles()) {
    const css = stripComments(read(file));
    // Split into rules and check any that both name a forbidden selector and
    // reference the brass token.
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1].trim();
      const body = match[2];
      if (!/--color-highlight\b/.test(body)) continue;
      for (const bad of forbidden) {
        if (selector.includes(bad)) offenders.push(`${file}: ${selector} uses brass`);
      }
    }
  }
  assert.deepEqual(offenders, [], offenders.join('\n'));
});

test('no progress-bar state changes colour by percentage', () => {
  const offenders = [];
  for (const file of cssFiles()) {
    if (/progress__fill--/.test(stripComments(read(file)))) {
      offenders.push(file);
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `a percentage-driven progress colour exists in: ${offenders.join(', ')}`
  );
});

test('the progress snippet emits one fill class only', () => {
  const s = stripLiquidComments(read('snippets/competition-progress.liquid'));
  assert.ok(!/bar_class/.test(s), 'progress still computes a conditional fill class');
  assert.match(s, /class="progress__fill"/);
});

/* -------------------------------------------------------------------------- *
 * One badge
 * -------------------------------------------------------------------------- */

test('the competition card requests exactly one badge', () => {
  const card = stripLiquidComments(read('snippets/competition-card.liquid'));
  assert.match(
    card,
    /render 'competition-badges', product: product, max: 1/,
    'the card must cap badges at one'
  );
});

test('the badge snippet defaults to one badge', () => {
  const badges = stripLiquidComments(read('snippets/competition-badges.liquid'));
  assert.match(badges, /assign max_badges = max \| default: 1/);
});

test('no brass badge variant exists', () => {
  for (const file of cssFiles()) {
    assert.ok(
      !/\.badge--highlight\b/.test(stripComments(read(file))),
      `${file} still defines a brass badge`
    );
  }
  for (const file of liquidFiles()) {
    assert.ok(
      !/badge--highlight/.test(read(file)),
      `${file} still uses a brass badge`
    );
  }
});

/* -------------------------------------------------------------------------- *
 * Brand names survive
 * -------------------------------------------------------------------------- */

test('no product or prize title passes through capitalize', () => {
  // Liquid's `capitalize` lowercases everything after the first character, so
  // "TaylorMade Qi10" becomes "Taylormade qi10". On a site whose proposition is
  // the brand of the prize, that is unacceptable.
  const offenders = [];
  for (const file of liquidFiles()) {
    const s = stripLiquidComments(read(file));
    s.split('\n').forEach((line, i) => {
      if (!/\|\s*capitalize/.test(line)) return;
      if (/title|product\.|prize|name|vendor/i.test(line)) {
        offenders.push(`${file}:${i + 1} — ${line.trim().slice(0, 90)}`);
      }
    });
  }
  assert.deepEqual(offenders, [], `capitalize applied to a name:\n${offenders.join('\n')}`);
});

/* -------------------------------------------------------------------------- *
 * Typography discipline
 * -------------------------------------------------------------------------- */

test('uppercase is confined to small labels', () => {
  // Uppercase is an emphasis device. Applied to headings, buttons and card
  // titles it flattens hierarchy, because nothing can then be emphasised
  // further. It survives only on labels at --text-xs or --text-sm.
  const allowedSizes = /var\(--text-xs\)|var\(--text-sm\)|0\.6875rem|0\.75rem/;
  const offenders = [];

  for (const file of cssFiles()) {
    const css = stripComments(read(file));
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1].trim();
      const body = match[2];
      if (!/text-transform:\s*uppercase/.test(body)) continue;
      // A token-driven transform is the merchant's choice, not a hard-coded one.
      if (/text-transform:\s*var\(/.test(body)) continue;
      if (!allowedSizes.test(body)) {
        offenders.push(`${file}: ${selector.replace(/\s+/g, ' ')}`);
      }
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `uppercase on something larger than a small label:\n${offenders.join('\n')}`
  );
});

test('a mono face is defined and used for numbers', () => {
  const vars = read('snippets/css-variables.liquid');
  assert.match(vars, /--font-mono-family/);

  const base = read('assets/base.css');
  assert.match(base, /\.num\s*\{/, 'no .num utility defined');
  assert.match(base, /font-variant-numeric:\s*tabular-nums/);

  // Prices, entry counts and dates carry it.
  const card = read('snippets/competition-card.liquid');
  assert.match(card, /competition-card__price-amount num/);
});

/* -------------------------------------------------------------------------- *
 * Spacing tiers
 * -------------------------------------------------------------------------- */

test('every section uses spacing tiers rather than raw padding', () => {
  const offenders = [];
  for (const f of fs.readdirSync(path.join(ROOT, 'sections'))) {
    if (!f.endsWith('.liquid')) continue;
    const s = read(path.join('sections', f));
    if (/padding_top|padding_bottom|padding_header/.test(s)) {
      offenders.push(`sections/${f}`);
    }
  }
  assert.deepEqual(offenders, [], `raw padding settings remain in:\n${offenders.join('\n')}`);
});

test('the three tiers are defined and distinct', () => {
  const vars = read('snippets/css-variables.liquid');
  for (const token of ['--space-major', '--space-standard', '--space-minor']) {
    assert.ok(vars.includes(token), `${token} is not defined`);
  }
  assert.match(vars, /--space-major:.*1\.75/);
  assert.match(vars, /--space-minor:.*0\.5/);
});

test('section-styles accepts tier names, not pixel values', () => {
  const s = read('snippets/section-styles.liquid');
  for (const tier of ['major', 'standard', 'minor', 'none']) {
    assert.ok(s.includes(`'${tier}'`) || s.includes(`when '${tier}'`), `tier ${tier} missing`);
  }
});

/* -------------------------------------------------------------------------- *
 * Card composition
 * -------------------------------------------------------------------------- */

test('the card has no container chrome', () => {
  // The photograph is the object. A bordered, shadowed box around it says
  // "this is a picture of a thing" rather than "this is the thing".
  const css = stripComments(read('assets/components.css'));
  const rule = css.match(/\.competition-card\s*\{([^}]*)\}/);
  assert.ok(rule, '.competition-card rule not found');
  assert.match(rule[1], /background:\s*none/);
  assert.match(rule[1], /border:\s*0/);
  assert.match(rule[1], /box-shadow:\s*none/);
});

test('entry progress is off on cards by default', () => {
  const schema = JSON.parse(read('config/settings_schema.json'));
  const comp = schema.find((g) => g.name === 't:settings.competition.name');
  const setting = comp.settings.find((s) => s.id === 'card_show_progress');
  assert.equal(setting.default, false, 'progress on a card is pressure, not information');
});

test('the competition grid is two-up on phones', () => {
  const css = stripComments(read('assets/components.css'));
  const rule = css.match(/\.competition-grid\s*\{([^}]*)\}/);
  assert.ok(rule, '.competition-grid rule not found');
  assert.match(rule[1], /grid-template-columns:\s*repeat\(2/);
});

/* -------------------------------------------------------------------------- *
 * Colour roles
 * -------------------------------------------------------------------------- */

test('the five colour roles are all defined with the agreed defaults', () => {
  const data = JSON.parse(read('config/settings_data.json')).current;
  const expected = {
    color_brand_deep: '#0c1f18',
    color_brand: '#1b4332',
    color_accent: '#417b25',
    color_page_background: '#f2f3ef',
    color_highlight: '#c9a961'
  };
  for (const [key, value] of Object.entries(expected)) {
    assert.equal(data[key], value, `${key} should be ${value}`);
  }
});

test('the brass used on light grounds is darkened enough to be legible', () => {
  // Bright brass reaches about 1.9:1 on paper. Anything reading as achievement
  // on a light ground uses the darkened token instead.
  const vars = read('snippets/css-variables.liquid');
  const match = vars.match(/assign brass_on_paper = brass \| color_darken:\s*(\d+)/);
  assert.ok(match, 'no darkened brass token is derived');
  assert.ok(
    Number(match[1]) >= 40,
    `brass darkened by only ${match[1]}%, which does not clear 4.5:1 on paper`
  );
});

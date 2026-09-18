/**
 * Structural checks on the theme itself.
 *
 * Theme Check covers Liquid correctness. These tests cover the things that
 * silently break a live store instead: a template pointing at a section that
 * was renamed, a `render` of a snippet that no longer exists, an asset
 * referenced but never shipped, or a translation key with no translation.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');

const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

// Markup checks must not trip over example markup inside documentation
// comments, so strip Liquid comments before scanning.
const readMarkup = (p) =>
  read(p)
    .replace(/\{%-?\s*comment\s*-?%\}[\s\S]*?\{%-?\s*endcomment\s*-?%\}/g, '')
    .replace(/\{%-?\s*#[\s\S]*?-?%\}/g, '');
const listFiles = (dir, ext) => {
  const full = path.join(ROOT, dir);
  if (!fs.existsSync(full)) return [];
  return fs
    .readdirSync(full, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith(ext))
    .map((entry) => entry.name);
};

const listTemplates = () => {
  const out = [];
  const walk = (rel) => {
    const full = path.join(ROOT, rel);
    for (const entry of fs.readdirSync(full, { withFileTypes: true })) {
      const next = path.join(rel, entry.name);
      if (entry.isDirectory()) walk(next);
      else if (entry.name.endsWith('.json')) out.push(next);
    }
  };
  walk('templates');
  return out;
};

/* -------------------------------------------------------------------------- *
 * Required structure
 * -------------------------------------------------------------------------- */

test('has every directory Shopify expects', () => {
  for (const dir of ['assets', 'config', 'layout', 'locales', 'sections', 'snippets', 'templates']) {
    assert.ok(fs.existsSync(path.join(ROOT, dir)), `missing ${dir}/`);
  }
});

test('ships the required layout and config files', () => {
  for (const file of [
    'layout/theme.liquid',
    'config/settings_schema.json',
    'config/settings_data.json',
    'locales/en.default.json',
    'locales/en.default.schema.json'
  ]) {
    assert.ok(fs.existsSync(path.join(ROOT, file)), `missing ${file}`);
  }
});

test('layout/theme.liquid contains the tags Shopify requires', () => {
  const layout = read('layout/theme.liquid');
  assert.match(layout, /\{\{\s*content_for_header\s*\}\}/);
  assert.match(layout, /\{\{\s*content_for_layout\s*\}\}/);
  assert.match(layout, /<html[^>]*lang=/);
});

/* -------------------------------------------------------------------------- *
 * Cross references
 * -------------------------------------------------------------------------- */

test('every JSON template references a section file that exists', () => {
  const sections = new Set(listFiles('sections', '.liquid').map((f) => f.replace('.liquid', '')));

  for (const template of listTemplates()) {
    const data = JSON.parse(read(template));
    for (const [key, section] of Object.entries(data.sections || {})) {
      assert.ok(
        sections.has(section.type),
        `${template} → section "${key}" uses missing type "${section.type}"`
      );
    }
    for (const key of data.order || []) {
      assert.ok(data.sections[key], `${template} orders "${key}" but never defines it`);
    }
  }
});

test('section groups reference section files that exist', () => {
  const sections = new Set(listFiles('sections', '.liquid').map((f) => f.replace('.liquid', '')));

  for (const file of listFiles('sections', '.json')) {
    const data = JSON.parse(read(path.join('sections', file)));
    for (const [key, section] of Object.entries(data.sections || {})) {
      assert.ok(
        sections.has(section.type),
        `sections/${file} → "${key}" uses missing type "${section.type}"`
      );
    }
    for (const key of data.order || []) {
      assert.ok(data.sections[key], `sections/${file} orders "${key}" but never defines it`);
    }
  }
});

test('every rendered snippet exists', () => {
  const snippets = new Set(listFiles('snippets', '.liquid').map((f) => f.replace('.liquid', '')));
  const sources = [
    ...listFiles('sections', '.liquid').map((f) => path.join('sections', f)),
    ...listFiles('snippets', '.liquid').map((f) => path.join('snippets', f)),
    ...listFiles('blocks', '.liquid').map((f) => path.join('blocks', f)),
    ...listFiles('layout', '.liquid').map((f) => path.join('layout', f)),
    'templates/gift_card.liquid'
  ];

  for (const source of sources) {
    const content = read(source);
    // {% render 'name' %} — the dynamic {% render block %} form is skipped.
    for (const match of content.matchAll(/\{%-?\s*render\s+'([a-z0-9_-]+)'/g)) {
      assert.ok(snippets.has(match[1]), `${source} renders missing snippet "${match[1]}"`);
    }
  }
});

test('every asset_url reference ships in assets/', () => {
  const assets = new Set(fs.readdirSync(path.join(ROOT, 'assets')));
  const sources = [
    ...listFiles('sections', '.liquid').map((f) => path.join('sections', f)),
    ...listFiles('snippets', '.liquid').map((f) => path.join('snippets', f)),
    ...listFiles('layout', '.liquid').map((f) => path.join('layout', f)),
    'templates/gift_card.liquid'
  ];

  for (const source of sources) {
    const content = read(source);
    for (const match of content.matchAll(/'([A-Za-z0-9_.-]+\.(?:css|js))'\s*\|\s*asset_url/g)) {
      assert.ok(assets.has(match[1]), `${source} references missing asset "${match[1]}"`);
    }
  }
});

/* -------------------------------------------------------------------------- *
 * Schemas
 * -------------------------------------------------------------------------- */

test('every section schema is valid JSON with a name', () => {
  for (const file of listFiles('sections', '.liquid')) {
    const content = read(path.join('sections', file));
    const match = content.match(/\{%\s*schema\s*%\}([\s\S]*?)\{%\s*endschema\s*%\}/);
    assert.ok(match, `sections/${file} has no schema`);

    let schema;
    assert.doesNotThrow(() => {
      schema = JSON.parse(match[1]);
    }, `sections/${file} schema is not valid JSON`);

    assert.ok(schema.name, `sections/${file} schema has no name`);

    for (const setting of schema.settings || []) {
      if (setting.type === 'header' || setting.type === 'paragraph') continue;
      assert.ok(setting.id, `sections/${file}: a ${setting.type} setting has no id`);
      assert.ok(setting.label, `sections/${file}: setting "${setting.id}" has no label`);
    }
  }
});

test('every theme block schema is valid JSON', () => {
  for (const file of listFiles('blocks', '.liquid')) {
    const content = read(path.join('blocks', file));
    const match = content.match(/\{%\s*schema\s*%\}([\s\S]*?)\{%\s*endschema\s*%\}/);
    assert.ok(match, `blocks/${file} has no schema`);
    assert.doesNotThrow(() => JSON.parse(match[1]), `blocks/${file} schema is not valid JSON`);
  }
});

test('config and locale files are valid JSON', () => {
  for (const file of ['config/settings_schema.json', 'config/settings_data.json']) {
    assert.doesNotThrow(() => JSON.parse(read(file)), `${file} is not valid JSON`);
  }
  for (const file of fs.readdirSync(path.join(ROOT, 'locales'))) {
    assert.doesNotThrow(() => JSON.parse(read(path.join('locales', file))), `${file} is not valid JSON`);
  }
});

test('settings_schema starts with theme_info', () => {
  const schema = JSON.parse(read('config/settings_schema.json'));
  assert.equal(schema[0].name, 'theme_info');
  assert.ok(schema[0].theme_name);
  assert.ok(schema[0].theme_version);
});

/* -------------------------------------------------------------------------- *
 * Translations
 * -------------------------------------------------------------------------- */

function flatten(obj, prefix = '', out = new Set()) {
  for (const [key, value] of Object.entries(obj)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      // one/other pluralisation groups are leaves, not namespaces.
      const keys = Object.keys(value);
      const isPlural = keys.every((k) =>
        ['zero', 'one', 'two', 'few', 'many', 'other'].includes(k)
      );
      if (isPlural) out.add(next);
      else flatten(value, next, out);
    } else {
      out.add(next);
    }
  }
  return out;
}

test('every storefront translation key used in Liquid exists', () => {
  const available = flatten(JSON.parse(read('locales/en.default.json')));
  const sources = [
    ...listFiles('sections', '.liquid').map((f) => path.join('sections', f)),
    ...listFiles('snippets', '.liquid').map((f) => path.join('snippets', f)),
    ...listFiles('blocks', '.liquid').map((f) => path.join('blocks', f)),
    ...listFiles('layout', '.liquid').map((f) => path.join('layout', f)),
    'templates/gift_card.liquid'
  ];

  const missing = [];
  for (const source of sources) {
    for (const match of read(source).matchAll(/'([a-z0-9_]+(?:\.[a-z0-9_]+)+)'\s*\|\s*t\b/g)) {
      if (!available.has(match[1])) missing.push(`${source}: ${match[1]}`);
    }
  }
  assert.deepEqual(missing, [], `missing translations:\n${missing.join('\n')}`);
});

test('every schema translation key used in a section exists', () => {
  const available = flatten(JSON.parse(read('locales/en.default.schema.json')));
  const missing = [];

  const files = [
    ...listFiles('sections', '.liquid').map((f) => path.join('sections', f)),
    ...listFiles('blocks', '.liquid').map((f) => path.join('blocks', f))
  ];

  for (const file of files) {
    for (const match of read(file).matchAll(/"t:([a-z0-9_.]+)"/g)) {
      if (!available.has(match[1])) missing.push(`${file}: t:${match[1]}`);
    }
  }

  for (const match of read('config/settings_schema.json').matchAll(/"t:([a-z0-9_.]+)"/g)) {
    if (!available.has(match[1])) missing.push(`settings_schema.json: t:${match[1]}`);
  }

  assert.deepEqual(missing, [], `missing schema translations:\n${missing.join('\n')}`);
});

/* -------------------------------------------------------------------------- *
 * Safety
 * -------------------------------------------------------------------------- */

test('no credentials or API keys are committed in theme assets', () => {
  const risky = /(sk_live_|pk_live_|shpat_|shpca_|shppa_|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)/;
  const dirs = ['assets', 'sections', 'snippets', 'layout', 'config', 'blocks'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      const content = read(path.join(dir, file));
      assert.ok(!risky.test(content), `${dir}/${file} looks like it contains a credential`);
    }
  }
});

test('no external script or stylesheet hosts are hard-coded', () => {
  // Third-party tags belong in Shopify apps or the custom-liquid section, not
  // baked into theme files where they cannot be audited or removed.
  const dirs = ['sections', 'snippets', 'layout', 'blocks'];
  const external = /<(?:script|link)[^>]+(?:src|href)=["']https?:\/\/(?!cdn\.shopify\.com)/i;

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      const content = read(path.join(dir, file));
      assert.ok(!external.test(content), `${dir}/${file} loads an external script or stylesheet`);
    }
  }
});

test('JavaScript assets parse', () => {
  const { execFileSync } = require('node:child_process');
  for (const file of fs.readdirSync(path.join(ROOT, 'assets')).filter((f) => f.endsWith('.js'))) {
    assert.doesNotThrow(
      () => execFileSync(process.execPath, ['--check', path.join(ROOT, 'assets', file)]),
      `assets/${file} has a syntax error`
    );
  }
});

test('no translation filter is applied after a default', () => {
  // Liquid applies filters left to right, so `value | default: 'some.key' | t`
  // runs the translation over the merchant's own text whenever they have set
  // one, producing "translation missing" on the storefront. Translate the
  // fallback into a variable first, then default to it.
  const offenders = [];
  const dirs = ['sections', 'snippets', 'layout', 'blocks'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const content = read(path.join(dir, file));
      content.split('\n').forEach((line, i) => {
        if (/\|\s*default:[^|]*\|\s*t\b/.test(line)) {
          offenders.push(`${dir}/${file}:${i + 1}`);
        }
      });
    }
  }

  assert.deepEqual(offenders, [], `default applied before t:\n${offenders.join('\n')}`);
});

test('interactive controls carry an accessible name', () => {
  // A bare icon button announces as "button" to a screen reader. Every button
  // in the theme must have visible text, aria-label, or aria-labelledby.
  const offenders = [];
  const dirs = ['sections', 'snippets', 'blocks'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const content = readMarkup(path.join(dir, file));

      for (const match of content.matchAll(/<button\b([\s\S]*?)>([\s\S]*?)<\/button>/g)) {
        const attrs = match[1];
        const inner = match[2];
        const hasLabel = /aria-label|aria-labelledby/.test(attrs);
        // Text that is not solely a render of an icon snippet.
        const withoutIcons = inner.replace(/\{%-?\s*render\s+'icon'[\s\S]*?-?%\}/g, '');
        const hasText = /[A-Za-z]/.test(withoutIcons.replace(/\{%[\s\S]*?%\}/g, '')) ||
          /\{\{/.test(withoutIcons);
        if (!hasLabel && !hasText) {
          offenders.push(`${dir}/${file}: ${match[0].slice(0, 70).replace(/\s+/g, ' ')}`);
        }
      }
    }
  }

  assert.deepEqual(offenders, [], `buttons with no accessible name:\n${offenders.join('\n')}`);
});

test('every image tag has an alt attribute', () => {
  const offenders = [];
  const dirs = ['sections', 'snippets', 'blocks', 'layout'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const content = readMarkup(path.join(dir, file));
      for (const match of content.matchAll(/<img\b([\s\S]*?)>/g)) {
        if (!/\balt=/.test(match[1])) {
          offenders.push(`${dir}/${file}: ${match[0].slice(0, 70).replace(/\s+/g, ' ')}`);
        }
      }
    }
  }

  assert.deepEqual(offenders, [], `images with no alt attribute:\n${offenders.join('\n')}`);
});

test('no inline event handler attributes are used', () => {
  // Inline handlers cannot be covered by a content security policy and mix
  // behaviour into markup. One exception is allowed and asserted explicitly.
  const offenders = [];
  const dirs = ['sections', 'snippets', 'blocks', 'layout'];
  const allowed = new Set(['sections/referral.liquid']);

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const rel = `${dir}/${file}`;
      const content = readMarkup(rel);
      for (const match of content.matchAll(/\son(click|change|submit|load|error|input)=/g)) {
        if (!allowed.has(rel)) offenders.push(`${rel}: on${match[1]}`);
      }
    }
  }

  assert.deepEqual(offenders, [], `inline handlers:\n${offenders.join('\n')}`);
});

test('every overlay element carries the shared overlay class', () => {
  // Overlay positioning is bound to the `overlay` class, not to the element
  // name, because gs-cart-drawer and gs-search-drawer extend gs-overlay in
  // JavaScript and CSS element selectors do not follow that inheritance.
  // An overlay without the class renders inline in the page.
  const offenders = [];
  const dirs = ['sections', 'snippets'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const content = readMarkup(path.join(dir, file));
      for (const match of content.matchAll(/<(gs-[a-z-]*(?:overlay|drawer|modal))\b([^>]*)>/g)) {
        const attrs = match[2];
        if (!/class="[^"]*\boverlay\b/.test(attrs)) {
          offenders.push(`${dir}/${file}: <${match[1]}> has no "overlay" class`);
        }
      }
    }
  }

  assert.deepEqual(offenders, [], offenders.join('\n'));
});

test('overlay styles are defined by class, not element name', () => {
  const css = read('assets/components.css').replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(/^\.overlay\s*\{/m.test(css), 'no .overlay rule found');
  assert.ok(!/^gs-overlay\s*[{[]/m.test(css), 'overlay is still styled by element name');
});

test('the hero always produces a heading, so the homepage has an h1', () => {
  // A page with no h1 is both an SEO and a screen-reader problem, and the
  // freshly installed state (no featured competition selected) used to produce
  // exactly that.
  const hero = read('sections/hero-competition.liquid');
  assert.match(hero, /<h1[^>]*>\{\{ heading \| escape \}\}<\/h1>/);
  assert.ok(
    /assign heading = shop\.name/.test(hero),
    'hero must fall back to the shop name so a heading always exists'
  );
});

test('no filter is applied inside a translation argument', () => {
  // `'key' | t: date: order.created_at | date: '%Y'` pipes the *translated
  // string* into the filter, not the argument. It produced "© now" in the
  // footer instead of the year. Build the value first, then pass it.
  const offenders = [];
  const dirs = ['sections', 'snippets', 'blocks', 'layout', 'templates'];

  for (const dir of dirs) {
    for (const file of fs.readdirSync(path.join(ROOT, dir))) {
      if (!file.endsWith('.liquid')) continue;
      const rel = path.join(dir, file);
      readMarkup(rel)
        .split('\n')
        .forEach((line, i) => {
          // A `| t:` followed by another pipe before the tag closes.
          if (/\|\s*t:\s*[^}]*\|/.test(line)) {
            offenders.push(`${rel}:${i + 1} — ${line.trim().slice(0, 90)}`);
          }
        });
    }
  }

  assert.deepEqual(offenders, [], `filter inside a t: argument:\n${offenders.join('\n')}`);
});

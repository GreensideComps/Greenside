/**
 * Recently viewed is first-party data held in the visitor's own browser. It
 * must never grow without bound, must not duplicate, and must degrade silently
 * where storage is unavailable (private browsing, blocked cookies).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTheme } = require('./helpers/load-theme.js');

test('most recent handle comes first and duplicates collapse', () => {
  const { Greenside } = loadTheme();
  Greenside.recentlyViewed.push('driver-competition');
  Greenside.recentlyViewed.push('putter-competition');
  Greenside.recentlyViewed.push('driver-competition');

  assert.deepEqual(Array.from(Greenside.recentlyViewed.read()), [
    'driver-competition',
    'putter-competition'
  ]);
});

test('list is capped so storage cannot grow without bound', () => {
  const { Greenside } = loadTheme();
  for (let i = 0; i < 40; i += 1) {
    Greenside.recentlyViewed.push('competition-' + i);
  }
  const stored = Greenside.recentlyViewed.read();
  assert.equal(stored.length, 12);
  assert.equal(stored[0], 'competition-39');
});

test('ignores empty handles', () => {
  const { Greenside } = loadTheme();
  Greenside.recentlyViewed.push('');
  Greenside.recentlyViewed.push(undefined);
  assert.deepEqual(Array.from(Greenside.recentlyViewed.read()), []);
});

test('returns an empty list when storage throws', () => {
  const { Greenside } = loadTheme({});
  // Simulate Safari private browsing, where setItem throws a quota error.
  const { sandbox } = loadTheme();
  sandbox.window.localStorage.setItem = () => {
    throw new Error('QuotaExceededError');
  };
  sandbox.window.localStorage.getItem = () => {
    throw new Error('SecurityError');
  };

  assert.doesNotThrow(() => sandbox.window.Greenside.recentlyViewed.push('x'));
  assert.deepEqual(Array.from(sandbox.window.Greenside.recentlyViewed.read()), []);
  assert.ok(Greenside);
});

test('recovers from corrupted storage content', () => {
  const { Greenside, sandbox } = loadTheme();
  sandbox.window.localStorage.setItem('greenside:recently-viewed', 'not json');
  assert.deepEqual(Array.from(Greenside.recentlyViewed.read()), []);

  sandbox.window.localStorage.setItem('greenside:recently-viewed', '{"not":"an array"}');
  assert.deepEqual(Array.from(Greenside.recentlyViewed.read()), []);
});

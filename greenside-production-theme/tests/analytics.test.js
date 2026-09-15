/**
 * The analytics layer must not fire duplicate events — a double-counted
 * add_to_cart corrupts conversion reporting — and must stay silent when the
 * merchant has not switched it on.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTheme } = require('./helpers/load-theme.js');

function withAnalytics(enabled) {
  const { Greenside, sandbox } = loadTheme({
    config: {
      routes: { root: '/', cart: '/cart' },
      moneyFormat: '£{{amount}}',
      currency: 'GBP',
      analytics: { enabled, debug: false },
      customer: { loggedIn: false }
    }
  });

  // theme.js emits page_view during boot. Tests assert on the events they
  // trigger themselves, so count only those.
  const eventsNamed = (name) =>
    (sandbox.window.dataLayer || []).filter((e) => e.event === 'greenside_' + name);

  return { Greenside, sandbox, eventsNamed };
}

test('emits page_view once on load', () => {
  const { eventsNamed } = withAnalytics(true);
  assert.equal(eventsNamed('page_view').length, 1);
});

test('stays completely silent when analytics is disabled', () => {
  const { Greenside, sandbox } = withAnalytics(false);
  Greenside.analytics.track('add_to_cart', { variant_id: 1 });
  // Not even page_view: nothing is written, and dataLayer is never created.
  assert.equal(sandbox.window.dataLayer, undefined);
});

test('pushes a namespaced event to dataLayer when enabled', () => {
  const { Greenside, eventsNamed } = withAnalytics(true);
  Greenside.analytics.track('add_to_cart', { variant_id: 42, quantity: 5 });

  const events = eventsNamed('add_to_cart');
  assert.equal(events.length, 1);
  const event = events[0];
  assert.equal(event.event, 'greenside_add_to_cart');
  assert.equal(event.variant_id, 42);
  assert.equal(event.quantity, 5);
  assert.equal(typeof event.timestamp, 'number');
});

test('suppresses an identical event fired twice in quick succession', () => {
  const { Greenside, eventsNamed } = withAnalytics(true);
  Greenside.analytics.track('add_to_cart', { variant_id: 42 });
  Greenside.analytics.track('add_to_cart', { variant_id: 42 });
  assert.equal(eventsNamed('add_to_cart').length, 1);
});

test('allows the same event name with a different payload', () => {
  const { Greenside, eventsNamed } = withAnalytics(true);
  Greenside.analytics.track('add_to_cart', { variant_id: 42 });
  Greenside.analytics.track('add_to_cart', { variant_id: 43 });
  assert.equal(eventsNamed('add_to_cart').length, 2);
});

test('ignores a track call with no event name', () => {
  const { Greenside, sandbox } = withAnalytics(true);
  const before = sandbox.window.dataLayer.length;
  Greenside.analytics.track('', { variant_id: 1 });
  Greenside.analytics.track(undefined, {});
  assert.equal(sandbox.window.dataLayer.length, before);
});

test('survives a payload that cannot be serialised', () => {
  const { Greenside, eventsNamed } = withAnalytics(true);
  const circular = {};
  circular.self = circular;
  // Must not throw: analytics can never be allowed to break a purchase.
  assert.doesNotThrow(() => Greenside.analytics.track('competition_view', circular));
  assert.equal(eventsNamed('competition_view').length, 1);
});

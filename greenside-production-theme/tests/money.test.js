/**
 * The money formatter is the one piece of pricing logic that runs client-side:
 * the live entry total on a competition page is computed and formatted here.
 * Getting it wrong shows a customer a price that differs from checkout, so it
 * is tested against every Shopify money format placeholder.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTheme } = require('./helpers/load-theme.js');

const { Greenside } = loadTheme();
const money = Greenside.money;

test('formats GBP with two decimals', () => {
  assert.equal(money(250, '£{{amount}}'), '£2.50');
  assert.equal(money(1250, '£{{amount}}'), '£12.50');
  assert.equal(money(0, '£{{amount}}'), '£0.00');
});

test('inserts thousands separators', () => {
  assert.equal(money(100000, '£{{amount}}'), '£1,000.00');
  assert.equal(money(1234567, '£{{amount}}'), '£12,345.67');
  assert.equal(money(100000000, '£{{amount}}'), '£1,000,000.00');
});

test('supports amount_no_decimals', () => {
  assert.equal(money(250000, '£{{amount_no_decimals}}'), '£2,500');
});

test('supports comma-separator formats used outside the UK', () => {
  assert.equal(money(123456, '{{amount_with_comma_separator}} €'), '1.234,56 €');
  assert.equal(money(123400, '{{amount_no_decimals_with_comma_separator}} €'), '1.234 €');
});

test('supports apostrophe and space separators', () => {
  assert.equal(money(123456, "CHF {{amount_with_apostrophe_separator}}"), "CHF 1'234.56");
  assert.equal(money(123456, '{{amount_with_space_separator}}'), '1 234,56');
});

test('tolerates whitespace inside the placeholder', () => {
  assert.equal(money(250, '£{{ amount }}'), '£2.50');
});

test('returns an empty string rather than NaN for bad input', () => {
  assert.equal(money('not a number', '£{{amount}}'), '');
  assert.equal(money(undefined, '£{{amount}}'), '');
  assert.equal(money(Infinity, '£{{amount}}'), '');
});

test('multiplying entries by unit price stays exact at realistic volumes', () => {
  // 10 entries at £2.49 must not drift into a floating point artefact.
  assert.equal(money(249 * 10, '£{{amount}}'), '£24.90');
  assert.equal(money(199 * 3, '£{{amount}}'), '£5.97');
  assert.equal(money(1099 * 25, '£{{amount}}'), '£274.75');
});

test('unknown placeholders resolve to empty rather than leaking the token', () => {
  assert.equal(money(250, '£{{bogus}}'), '£');
});

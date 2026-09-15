/**
 * Loads assets/theme.js into a minimal DOM stub so its exports can be tested
 * in Node without a browser or a bundler.
 *
 * The stub implements only what theme.js touches at load time. Anything it
 * does not implement would throw, which is deliberate: it keeps the file
 * honest about what it depends on during boot.
 */
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function createElementStub() {
  return {
    textContent: '',
    hidden: false,
    style: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {},
    getAttribute: () => null,
    removeAttribute() {},
    hasAttribute: () => false,
    appendChild() {},
    removeChild() {},
    addEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    focus() {},
    closest: () => null
  };
}

function loadTheme(options = {}) {
  const config = options.config || {
    routes: { root: '/', cart: '/cart', cartAdd: '/cart/add.js', cartChange: '/cart/change.js' },
    cartBehaviour: 'drawer',
    moneyFormat: '£{{amount}}',
    currency: 'GBP',
    analytics: { enabled: false, debug: false },
    customer: { loggedIn: false }
  };

  const strings = options.strings || { cartError: 'Something went wrong.' };

  const jsonIslands = {
    GsConfig: JSON.stringify(config),
    GsStrings: JSON.stringify(strings),
    GsPageMeta: JSON.stringify(options.pageMeta || { pageType: 'index', template: 'index' })
  };

  const listeners = {};

  const documentStub = {
    readyState: 'complete',
    documentElement: createElementStub(),
    body: createElementStub(),
    getElementById(id) {
      if (Object.prototype.hasOwnProperty.call(jsonIslands, id)) {
        return { textContent: jsonIslands[id] };
      }
      return null;
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => createElementStub(),
    addEventListener(name, handler) {
      listeners[name] = listeners[name] || [];
      listeners[name].push(handler);
    },
    removeEventListener() {},
    dispatchEvent: () => true,
    contains: () => false,
    activeElement: null
  };

  // Custom element registration is a no-op: these tests target pure logic.
  const registry = new Map();
  const customElementsStub = {
    define(name, ctor) {
      registry.set(name, ctor);
    },
    get(name) {
      return registry.get(name);
    }
  };

  const windowStub = {
    location: { pathname: '/', href: 'https://example.com/', search: '' },
    localStorage: createStorageStub(),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    getComputedStyle: () => ({ visibility: 'visible' }),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    addEventListener(name, handler) {
      listeners[name] = listeners[name] || [];
      listeners[name].push(handler);
    },
    removeEventListener() {},
    alert() {},
    innerWidth: 1280,
    isSecureContext: true
  };

  const sandbox = {
    window: windowStub,
    document: documentStub,
    customElements: customElementsStub,
    console: { warn() {}, info() {}, error() {} },
    fetch: options.fetch || (() => Promise.reject(new Error('fetch not stubbed'))),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    HTMLElement: class HTMLElement {},
    EventTarget,
    CustomEvent,
    Event,
    Map,
    Set,
    JSON,
    Date,
    Math,
    Number,
    String,
    Object,
    Array,
    isFinite,
    navigator: { clipboard: null, share: null },
    URLSearchParams,
    FormData: class FormData {},
    DOMParser: class DOMParser {},
    AbortController: class AbortController {},
    Promise
  };

  sandbox.window.window = sandbox.window;
  sandbox.globalThis = sandbox;

  vm.createContext(sandbox);

  const source = fs.readFileSync(path.join(__dirname, '..', '..', 'assets', 'theme.js'), 'utf8');
  vm.runInContext(source, sandbox, { filename: 'theme.js' });

  return { Greenside: sandbox.window.Greenside, sandbox, listeners };
}

function createStorageStub() {
  const store = new Map();
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear()
  };
}

module.exports = { loadTheme, createStorageStub };

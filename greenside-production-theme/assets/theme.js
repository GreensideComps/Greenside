/**
 * Greenside — core theme script.
 *
 * No framework and no dependencies. Everything is a custom element so sections
 * can be added, removed and reordered in the theme editor without re-binding.
 *
 * Public surface (window.Greenside):
 *   config        parsed #GsConfig
 *   strings       parsed #GsStrings
 *   money(cents)  format using the shop's money format
 *   announce(msg) push a message into the shared aria-live region
 *   analytics     { track(name, payload) }
 *   cart          { add, change, get, getState }
 *   events        { on, off, emit }
 */
(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Config and strings
   * ------------------------------------------------------------------ */

  function readJSON(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback;
    try {
      return JSON.parse(el.textContent);
    } catch (error) {
      console.warn('[Greenside] Could not parse #' + id, error);
      return fallback;
    }
  }

  var config = readJSON('GsConfig', {
    routes: {},
    cartBehaviour: 'drawer',
    moneyFormat: '£{{amount}}',
    analytics: { enabled: false, debug: false },
    customer: { loggedIn: false }
  });

  var strings = readJSON('GsStrings', {});

  /* ------------------------------------------------------------------ *
   * Small utilities
   * ------------------------------------------------------------------ */

  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), summary, ' +
    '[tabindex]:not([tabindex="-1"])';

  function $(selector, scope) {
    return (scope || document).querySelector(selector);
  }

  function $$(selector, scope) {
    return Array.prototype.slice.call((scope || document).querySelectorAll(selector));
  }

  function debounce(fn, wait) {
    var timer;
    return function () {
      var args = arguments;
      var context = this;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(context, args);
      }, wait);
    };
  }

  /**
   * Format an amount in minor units (pence) with the shop's money format.
   * Supports the four Shopify placeholders. Falls back to a plain number if the
   * format string is something unexpected.
   */
  function money(cents, format) {
    var value = Number(cents);
    if (!isFinite(value)) return '';
    var fmt = format || config.moneyFormat || '£{{amount}}';

    function withCommas(number) {
      return number.replace(/(\d)(?=(\d\d\d)+(?!\d))/g, '$1,');
    }

    function formatWithDelimiters(number, precision, thousands, decimal) {
      thousands = thousands || ',';
      decimal = decimal || '.';
      var fixed = (number / 100.0).toFixed(precision);
      var parts = fixed.split('.');
      var dollars = withCommas(parts[0]).replace(/,/g, thousands);
      var centsPart = parts[1] ? decimal + parts[1] : '';
      return dollars + centsPart;
    }

    return fmt.replace(/\{\{\s*(\w+)\s*\}\}/g, function (_match, name) {
      switch (name) {
        case 'amount':
          return formatWithDelimiters(value, 2);
        case 'amount_no_decimals':
          return formatWithDelimiters(value, 0);
        case 'amount_with_comma_separator':
          return formatWithDelimiters(value, 2, '.', ',');
        case 'amount_no_decimals_with_comma_separator':
          return formatWithDelimiters(value, 0, '.', ',');
        case 'amount_with_apostrophe_separator':
          return formatWithDelimiters(value, 2, "'", '.');
        case 'amount_no_decimals_with_space_separator':
          return formatWithDelimiters(value, 0, ' ', ',');
        case 'amount_with_space_separator':
          return formatWithDelimiters(value, 2, ' ', ',');
        default:
          return '';
      }
    });
  }

  var liveRegion = null;
  var announceTimer = null;

  /** Speak a short message through the single shared aria-live region. */
  function announce(message) {
    if (!message) return;
    liveRegion = liveRegion || document.getElementById('GsLiveRegion');
    if (!liveRegion) return;
    // Clearing first makes screen readers re-announce an identical message.
    liveRegion.textContent = '';
    clearTimeout(announceTimer);
    announceTimer = setTimeout(function () {
      liveRegion.textContent = message;
    }, 60);
  }

  /* ------------------------------------------------------------------ *
   * Event bus
   * ------------------------------------------------------------------ */

  var events = (function () {
    var target = new EventTarget();
    return {
      on: function (name, handler) {
        target.addEventListener(name, handler);
        return function () {
          target.removeEventListener(name, handler);
        };
      },
      off: function (name, handler) {
        target.removeEventListener(name, handler);
      },
      emit: function (name, detail) {
        target.dispatchEvent(new CustomEvent(name, { detail: detail }));
      }
    };
  })();

  /* ------------------------------------------------------------------ *
   * Analytics
   *
   * One documented event set, emitted once per logical action. Every event
   * goes to window.dataLayer and to a `greenside:analytics` DOM event so a tag
   * manager, a Shopify Web Pixel or a custom listener can all consume the same
   * stream. See docs/ANALYTICS.md.
   * ------------------------------------------------------------------ */

  var analytics = (function () {
    var settings = config.analytics || {};
    var recent = new Map();
    // Actions that fire at most once per payload within this window.
    var DEDUPE_MS = 800;

    function track(name, payload) {
      if (!settings.enabled) return;
      if (!name) return;

      var data = payload || {};
      var key = name + '|' + safeKey(data);
      var now = Date.now();
      var last = recent.get(key);
      if (last && now - last < DEDUPE_MS) return;
      recent.set(key, now);

      var event = Object.assign({ event: 'greenside_' + name, timestamp: now }, data);

      window.dataLayer = window.dataLayer || [];
      window.dataLayer.push(event);

      document.dispatchEvent(
        new CustomEvent('greenside:analytics', { detail: { name: name, payload: data } })
      );

      if (settings.debug) {
        console.info('[Greenside analytics]', name, data);
      }
    }

    function safeKey(data) {
      try {
        return JSON.stringify(data);
      } catch (error) {
        return String(data);
      }
    }

    return { track: track };
  })();

  /* ------------------------------------------------------------------ *
   * Cart
   * ------------------------------------------------------------------ */

  var cart = (function () {
    var state = null;

    function request(url, body) {
      return fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json'
        },
        body: JSON.stringify(body)
      }).then(function (response) {
        return response.json().then(function (json) {
          if (!response.ok) {
            var error = new Error(json.description || json.message || strings.cartError);
            error.data = json;
            error.status = response.status;
            throw error;
          }
          return json;
        });
      });
    }

    function get() {
      return fetch(config.routes.cart + '.js', {
        headers: { Accept: 'application/json' }
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (json) {
          state = json;
          return json;
        });
    }

    function getState() {
      return state;
    }

    /**
     * Add an item. `sections` asks Shopify to re-render the cart UI in the same
     * round trip, which keeps the drawer in sync without a second request.
     */
    function add(payload, sections) {
      var body = Object.assign({}, payload);
      if (sections && sections.length) {
        body.sections = sections.join(',');
        body.sections_url = window.location.pathname;
      }
      return request(config.routes.cartAdd, body).then(function (json) {
        return get().then(function (cartState) {
          events.emit('cart:updated', { cart: cartState, sections: json.sections, added: json });
          return json;
        });
      });
    }

    function change(payload, sections) {
      var body = Object.assign({}, payload);
      if (sections && sections.length) {
        body.sections = sections.join(',');
        body.sections_url = window.location.pathname;
      }
      return request(config.routes.cartChange, body).then(function (json) {
        state = json;
        events.emit('cart:updated', { cart: json, sections: json.sections });
        return json;
      });
    }

    return { add: add, change: change, get: get, getState: getState };
  })();

  /* ------------------------------------------------------------------ *
   * Focus management
   * ------------------------------------------------------------------ */

  var scrollLocks = 0;

  function lockScroll() {
    scrollLocks += 1;
    if (scrollLocks > 1) return;
    var scrollbar = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.paddingRight = scrollbar > 0 ? scrollbar + 'px' : '';
    document.body.style.overflow = 'hidden';
  }

  function unlockScroll() {
    scrollLocks = Math.max(0, scrollLocks - 1);
    if (scrollLocks > 0) return;
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
  }

  function focusableWithin(element) {
    return $$(FOCUSABLE, element).filter(function (el) {
      return (
        !el.hasAttribute('hidden') &&
        el.offsetParent !== null &&
        window.getComputedStyle(el).visibility !== 'hidden'
      );
    });
  }

  /* ------------------------------------------------------------------ *
   * <gs-overlay> — shared base for drawers, modals and menus.
   *
   * Attributes:
   *   open              present while visible
   *   data-close-on-esc "false" to opt out
   * Children:
   *   [data-overlay-panel]  the focus trap container
   *   [data-overlay-close]  any number of close buttons
   *   [data-overlay-scrim]  click-to-dismiss backdrop
   * ------------------------------------------------------------------ */

  class GsOverlay extends HTMLElement {
    constructor() {
      super();
      this.onKeydown = this.onKeydown.bind(this);
      this.onClick = this.onClick.bind(this);
      this.opener = null;
    }

    connectedCallback() {
      this.addEventListener('click', this.onClick);
      this.setAttribute('role', this.getAttribute('role') || 'dialog');
      this.setAttribute('aria-modal', 'true');
      if (!this.hasAttribute('open')) this.setAttribute('inert', '');
    }

    disconnectedCallback() {
      document.removeEventListener('keydown', this.onKeydown);
      if (this.hasAttribute('open')) unlockScroll();
    }

    get panel() {
      return this.querySelector('[data-overlay-panel]') || this;
    }

    onClick(event) {
      if (event.target.closest('[data-overlay-close]')) {
        event.preventDefault();
        this.close();
        return;
      }
      if (event.target.hasAttribute('data-overlay-scrim')) {
        this.close();
      }
    }

    onKeydown(event) {
      if (event.key === 'Escape' && this.getAttribute('data-close-on-esc') !== 'false') {
        event.stopPropagation();
        this.close();
        return;
      }
      if (event.key !== 'Tab') return;

      var items = focusableWithin(this.panel);
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    open(opener) {
      if (this.hasAttribute('open')) return;
      this.opener = opener || document.activeElement;
      this.removeAttribute('inert');
      this.setAttribute('open', '');
      lockScroll();
      document.addEventListener('keydown', this.onKeydown);

      // Wait a frame so the panel has laid out before moving focus into it.
      requestAnimationFrame(() => {
        var target =
          this.querySelector('[data-overlay-initial-focus]') || focusableWithin(this.panel)[0];
        if (target) target.focus();
      });

      this.dispatchEvent(new CustomEvent('gs:overlay:open', { bubbles: true }));
    }

    close() {
      if (!this.hasAttribute('open')) return;
      this.removeAttribute('open');
      unlockScroll();
      document.removeEventListener('keydown', this.onKeydown);

      // Hide from assistive tech once the close transition has finished.
      var restore = this.opener;
      this.opener = null;
      window.setTimeout(() => {
        if (!this.hasAttribute('open')) this.setAttribute('inert', '');
      }, 300);

      if (restore && document.contains(restore)) {
        restore.focus();
      }

      this.dispatchEvent(new CustomEvent('gs:overlay:close', { bubbles: true }));
    }

    toggle(opener) {
      if (this.hasAttribute('open')) {
        this.close();
      } else {
        this.open(opener);
      }
    }
  }

  customElements.define('gs-overlay', GsOverlay);

  /* ------------------------------------------------------------------ *
   * [data-overlay-trigger] — declarative opener for any overlay by id.
   * ------------------------------------------------------------------ */

  document.addEventListener('click', function (event) {
    var trigger = event.target.closest('[data-overlay-trigger]');
    if (!trigger) return;
    var overlay = document.getElementById(trigger.getAttribute('data-overlay-trigger'));
    if (!overlay || typeof overlay.toggle !== 'function') return;
    event.preventDefault();
    overlay.toggle(trigger);
  });

  /* ------------------------------------------------------------------ *
   * <gs-quantity> — accessible number stepper.
   *
   * Emits `gs:quantity:change` with { value, previous }.
   * ------------------------------------------------------------------ */

  class GsQuantity extends HTMLElement {
    connectedCallback() {
      this.input = this.querySelector('input');
      if (!this.input) return;

      this.decrementButton = this.querySelector('[data-quantity-decrease]');
      this.incrementButton = this.querySelector('[data-quantity-increase]');

      this.addEventListener('click', (event) => {
        var button = event.target.closest('[data-quantity-decrease], [data-quantity-increase]');
        if (!button) return;
        event.preventDefault();
        this.step(button.hasAttribute('data-quantity-increase') ? 1 : -1);
      });

      this.input.addEventListener('change', () => this.commit());
      this.input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          this.commit();
        }
      });

      this.syncButtons();
    }

    get min() {
      return Number(this.input.min || 1);
    }

    get max() {
      var max = this.input.max;
      return max === '' || max == null ? Infinity : Number(max);
    }

    get step() {
      return Number(this.input.step || 1);
    }

    get value() {
      return Number(this.input.value || this.min);
    }

    /** Clamp to [min, max] and round to the nearest valid step. */
    clamp(value) {
      var stepSize = Number(this.input.step || 1) || 1;
      var minimum = this.min;
      var rounded = minimum + Math.round((value - minimum) / stepSize) * stepSize;
      return Math.min(Math.max(rounded, minimum), this.max);
    }

    step(direction) {
      var stepSize = Number(this.input.step || 1) || 1;
      this.setValue(this.value + direction * stepSize);
    }

    setValue(next) {
      var previous = this.value;
      var clamped = this.clamp(Number(next));
      if (!isFinite(clamped)) return;
      this.input.value = String(clamped);
      this.syncButtons();
      if (clamped !== previous) {
        this.dispatchEvent(
          new CustomEvent('gs:quantity:change', {
            bubbles: true,
            detail: { value: clamped, previous: previous }
          })
        );
      }
    }

    commit() {
      var raw = this.input.value.replace(/[^\d]/g, '');
      if (raw === '') {
        this.setValue(this.min);
        return;
      }
      this.setValue(Number(raw));
    }

    syncButtons() {
      if (this.decrementButton) this.decrementButton.disabled = this.value <= this.min;
      if (this.incrementButton) this.incrementButton.disabled = this.value >= this.max;
    }
  }

  customElements.define('gs-quantity', GsQuantity);

  /* ------------------------------------------------------------------ *
   * <gs-add-to-cart> — wraps a product form.
   * ------------------------------------------------------------------ */

  class GsAddToCart extends HTMLElement {
    connectedCallback() {
      this.form = this.querySelector('form');
      if (!this.form) return;
      this.submitButton = this.querySelector('[type="submit"]');
      this.errorTarget = this.querySelector('[data-cart-error]');
      this.form.addEventListener('submit', this.onSubmit.bind(this));
    }

    onSubmit(event) {
      // Without JS the form posts normally, so only intercept when we can.
      event.preventDefault();
      if (this.submitButton && this.submitButton.getAttribute('aria-disabled') === 'true') return;

      this.setBusy(true);
      this.clearError();

      var formData = new FormData(this.form);
      var payload = { items: [{}] };
      var item = payload.items[0];

      formData.forEach(function (value, key) {
        if (key === 'id') item.id = Number(value);
        else if (key === 'quantity') item.quantity = Number(value);
        else if (key.indexOf('properties[') === 0) {
          item.properties = item.properties || {};
          item.properties[key.slice(11, -1)] = value;
        } else if (key === 'selling_plan') item.selling_plan = Number(value);
      });

      if (!item.id) {
        this.setBusy(false);
        this.showError(strings.cartError);
        return;
      }

      var sections = config.cartBehaviour === 'drawer' ? ['cart-drawer', 'cart-icon'] : ['cart-icon'];

      cart
        .add(payload, sections)
        .then((response) => {
          var added = (response.items && response.items[0]) || response;
          analytics.track('add_to_cart', {
            product_id: added.product_id,
            variant_id: added.variant_id || added.id,
            product_title: added.product_title || added.title,
            quantity: item.quantity || 1,
            price: added.price,
            currency: config.currency
          });
          announce(strings.addedToCart);

          if (config.cartBehaviour === 'drawer') {
            var drawer = document.getElementById('CartDrawer');
            if (drawer && typeof drawer.open === 'function') drawer.open(this.submitButton);
          } else {
            window.location.href = config.routes.cart;
          }
        })
        .catch((error) => {
          this.showError(error.message || strings.cartError);
        })
        .finally(() => {
          this.setBusy(false);
        });
    }

    setBusy(busy) {
      if (!this.submitButton) return;
      this.submitButton.setAttribute('aria-busy', busy ? 'true' : 'false');
      this.submitButton.setAttribute('aria-disabled', busy ? 'true' : 'false');
    }

    clearError() {
      if (!this.errorTarget) return;
      this.errorTarget.textContent = '';
      this.errorTarget.hidden = true;
    }

    showError(message) {
      announce(message);
      if (!this.errorTarget) {
        window.alert(message);
        return;
      }
      this.errorTarget.textContent = message;
      this.errorTarget.hidden = false;
    }
  }

  customElements.define('gs-add-to-cart', GsAddToCart);

  /* ------------------------------------------------------------------ *
   * <gs-copy> — copy a value to the clipboard with visible confirmation.
   * ------------------------------------------------------------------ */

  class GsCopy extends HTMLElement {
    connectedCallback() {
      this.button = this.querySelector('button');
      this.feedback = this.querySelector('[data-copy-feedback]');
      if (!this.button) return;
      this.button.addEventListener('click', () => this.copy());
    }

    copy() {
      var value = this.getAttribute('value') || '';
      if (!value) return;

      var done = () => {
        announce(strings.copied);
        if (this.feedback) {
          this.feedback.hidden = false;
          clearTimeout(this.timer);
          this.timer = setTimeout(() => {
            this.feedback.hidden = true;
          }, 2500);
        }
        this.dispatchEvent(new CustomEvent('gs:copied', { bubbles: true }));
      };

      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(value).then(done).catch(() => this.fallbackCopy(value, done));
      } else {
        this.fallbackCopy(value, done);
      }
    }

    fallbackCopy(value, done) {
      var field = document.createElement('textarea');
      field.value = value;
      field.setAttribute('readonly', '');
      field.style.position = 'fixed';
      field.style.opacity = '0';
      document.body.appendChild(field);
      field.select();
      try {
        document.execCommand('copy');
        done();
      } catch (error) {
        /* Clipboard unavailable — the value stays visible for manual copying. */
      }
      document.body.removeChild(field);
    }
  }

  customElements.define('gs-copy', GsCopy);

  /* ------------------------------------------------------------------ *
   * <gs-carousel> — scroll-snap rail with buttons and keyboard support.
   * ------------------------------------------------------------------ */

  class GsCarousel extends HTMLElement {
    connectedCallback() {
      this.track = this.querySelector('[data-carousel-track]');
      this.prevButton = this.querySelector('[data-carousel-prev]');
      this.nextButton = this.querySelector('[data-carousel-next]');
      if (!this.track) return;

      this.update = debounce(this.updateButtons.bind(this), 100);
      this.track.addEventListener('scroll', this.update, { passive: true });
      window.addEventListener('resize', this.update, { passive: true });

      if (this.prevButton) this.prevButton.addEventListener('click', () => this.scrollByPage(-1));
      if (this.nextButton) this.nextButton.addEventListener('click', () => this.scrollByPage(1));

      this.updateButtons();
    }

    disconnectedCallback() {
      window.removeEventListener('resize', this.update);
    }

    scrollByPage(direction) {
      var item = this.track.firstElementChild;
      var amount = item ? item.getBoundingClientRect().width + 16 : this.track.clientWidth;
      this.track.scrollBy({ left: direction * amount, behavior: 'smooth' });
    }

    updateButtons() {
      if (!this.track) return;
      var maxScroll = this.track.scrollWidth - this.track.clientWidth;
      var atStart = this.track.scrollLeft <= 2;
      var atEnd = this.track.scrollLeft >= maxScroll - 2;
      var scrollable = maxScroll > 4;

      if (this.prevButton) this.prevButton.disabled = atStart;
      if (this.nextButton) this.nextButton.disabled = atEnd;
      this.toggleAttribute('data-scrollable', scrollable);
    }
  }

  customElements.define('gs-carousel', GsCarousel);

  /* ------------------------------------------------------------------ *
   * <gs-newsletter> — progressive enhancement over Shopify's customer form.
   *
   * There is no bespoke transport here: the form posts to Shopify, which is
   * what every major CRM (Klaviyo included) syncs from. The analytics event
   * gives a provider a hook without the theme owning the integration.
   * ------------------------------------------------------------------ */

  class GsNewsletter extends HTMLElement {
    connectedCallback() {
      this.form = this.querySelector('form');
      if (!this.form) return;

      // Shopify reloads with ?customer_posted=true on success.
      if (this.querySelector('[data-newsletter-success]')) {
        analytics.track('email_signup', { location: this.dataset.location || 'unknown' });
      }

      this.form.addEventListener('submit', () => {
        var button = this.querySelector('[type="submit"]');
        if (button) button.setAttribute('aria-busy', 'true');
      });
    }
  }

  customElements.define('gs-newsletter', GsNewsletter);

  /* ------------------------------------------------------------------ *
   * Page-level analytics
   * ------------------------------------------------------------------ */

  function bootstrapAnalytics() {
    var meta = document.getElementById('GsPageMeta');
    var page = meta ? readJSON('GsPageMeta', {}) : {};

    analytics.track('page_view', {
      page_type: page.pageType || 'unknown',
      template: page.template || '',
      title: document.title,
      path: window.location.pathname,
      logged_in: !!(config.customer && config.customer.loggedIn)
    });

    if (page.pageType === 'product' && page.product) {
      analytics.track('competition_view', page.product);
    }

    if (page.pageType === 'search' && page.search) {
      analytics.track('search', page.search);
    }

    if (page.pageType === 'cart') {
      analytics.track('view_cart', { value: page.cartTotal, currency: config.currency });
    }
  }

  // Delegated tracking for links that opt in with data-track.
  document.addEventListener('click', function (event) {
    var el = event.target.closest('[data-track]');
    if (!el) return;
    var name = el.getAttribute('data-track');
    var payload = {};
    try {
      payload = el.getAttribute('data-track-payload')
        ? JSON.parse(el.getAttribute('data-track-payload'))
        : {};
    } catch (error) {
      payload = {};
    }
    analytics.track(name, payload);
  });

  document.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-track-submit]');
    if (!form) return;
    analytics.track(form.getAttribute('data-track-submit'), {});
  });

  /* ------------------------------------------------------------------ *
   * Recently viewed — first-party, local only.
   * ------------------------------------------------------------------ */

  var recentlyViewed = (function () {
    var KEY = 'greenside:recently-viewed';
    var LIMIT = 12;

    function read() {
      try {
        var raw = window.localStorage.getItem(KEY);
        var parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed : [];
      } catch (error) {
        return [];
      }
    }

    function push(handle) {
      if (!handle) return;
      try {
        var list = read().filter(function (item) {
          return item !== handle;
        });
        list.unshift(handle);
        window.localStorage.setItem(KEY, JSON.stringify(list.slice(0, LIMIT)));
      } catch (error) {
        /* Private mode or storage disabled — recently viewed simply stays off. */
      }
    }

    return { read: read, push: push };
  })();

  /* ------------------------------------------------------------------ *
   * Boot
   * ------------------------------------------------------------------ */

  function boot() {
    bootstrapAnalytics();

    var meta = readJSON('GsPageMeta', {});
    if (meta.pageType === 'product' && meta.product && meta.product.handle) {
      recentlyViewed.push(meta.product.handle);
    }

    // Keep the cart icon count fresh when returning via the back/forward cache.
    window.addEventListener('pageshow', function (event) {
      if (event.persisted) {
        cart.get().catch(function () {});
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  /* ------------------------------------------------------------------ *
   * Export
   * ------------------------------------------------------------------ */

  window.Greenside = {
    config: config,
    strings: strings,
    money: money,
    announce: announce,
    analytics: analytics,
    cart: cart,
    events: events,
    recentlyViewed: recentlyViewed,
    utils: { $: $, $$: $$, debounce: debounce, focusableWithin: focusableWithin }
  };
})();

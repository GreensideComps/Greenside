/**
 * Cart drawer and cart page behaviour.
 *
 * Every mutation goes through Shopify's Cart API asking for the relevant
 * sections back in the same response, so the drawer, the cart page and the
 * header count all update from one round trip.
 */
(function () {
  'use strict';

  var GS = window.Greenside;
  if (!GS) return;

  /** Replace an element's innerHTML from a returned section's markup. */
  function replaceFrom(html, selector, target) {
    if (!html || !target) return;
    var parsed = new DOMParser().parseFromString(html, 'text/html');
    var source = parsed.querySelector(selector);
    if (source) target.innerHTML = source.innerHTML;
  }

  /** Keep the header bubble in step with the cart. */
  function updateCartCount(count) {
    var bubbles = document.querySelectorAll('[data-cart-count]');
    bubbles.forEach(function (bubble) {
      bubble.textContent = count;
      bubble.hidden = count === 0;
    });

    document.querySelectorAll('[data-overlay-trigger="CartDrawer"], a[href$="/cart"]').forEach(function (el) {
      var label = el.getAttribute('aria-label');
      if (label) el.setAttribute('aria-label', label.replace(/\d+/, count));
    });
  }

  /* ------------------------------------------------------------------ *
   * <gs-cart-drawer>
   * ------------------------------------------------------------------ */

  class GsCartDrawer extends (customElements.get('gs-overlay') || HTMLElement) {
    connectedCallback() {
      if (super.connectedCallback) super.connectedCallback();
      this.bind();
      this.unsubscribe = GS.events.on('cart:updated', this.onCartUpdated.bind(this));
    }

    disconnectedCallback() {
      if (super.disconnectedCallback) super.disconnectedCallback();
      if (this.unsubscribe) this.unsubscribe();
    }

    bind() {
      this.addEventListener('gs:quantity:change', (event) => {
        var input = event.target.querySelector('[data-cart-quantity]');
        if (!input) return;
        this.changeLine(input.dataset.lineKey, event.detail.value, event.target);
      });

      this.addEventListener('click', (event) => {
        var remove = event.target.closest('[data-cart-remove]');
        if (!remove) return;
        event.preventDefault();
        this.changeLine(remove.dataset.lineKey, 0, remove.closest('[data-cart-item]'));
      });
    }

    changeLine(key, quantity, scope) {
      if (!key) return;
      var item = scope ? scope.closest('[data-cart-item]') : null;
      if (item) item.setAttribute('aria-busy', 'true');

      var line = item ? item.querySelector('[data-cart-item-title]') : null;
      var previousItem = item
        ? {
            title: (item.querySelector('.cart-item__title') || {}).textContent,
            quantity: Number((item.querySelector('[data-cart-quantity]') || {}).value || 0)
          }
        : null;

      GS.cart
        .change({ id: key, quantity: quantity }, ['cart-drawer', 'cart-icon', 'main-cart'])
        .then((cartState) => {
          if (quantity === 0 && previousItem) {
            GS.analytics.track('remove_from_cart', {
              line_key: key,
              product_title: previousItem.title,
              quantity: previousItem.quantity
            });
          }
          this.applySections(cartState);
          GS.announce(GS.strings.loading);
        })
        .catch((error) => {
          this.showLineError(item, error.message || GS.strings.quantityError);
        })
        .finally(() => {
          if (item) item.removeAttribute('aria-busy');
        });
    }

    showLineError(item, message) {
      GS.announce(message);
      if (!item) return;
      var target = item.querySelector('[data-line-error]');
      if (!target) return;
      target.textContent = message;
      target.hidden = false;
    }

    applySections(cartState) {
      var sections = cartState && cartState.sections;
      updateCartCount(cartState && typeof cartState.item_count === 'number' ? cartState.item_count : 0);

      if (!sections) {
        // No sections came back (a direct API change) — reload the drawer.
        this.refresh();
        return;
      }

      if (sections['cart-drawer']) {
        var wasOpen = this.hasAttribute('open');
        replaceFrom(sections['cart-drawer'], '.overlay__panel', this.querySelector('.overlay__panel'));
        if (wasOpen) this.setAttribute('open', '');
      }

      var mainCart = document.getElementById('MainCart');
      if (sections['main-cart'] && mainCart) {
        replaceFrom(sections['main-cart'], '#MainCart', mainCart);
      }
    }

    onCartUpdated(event) {
      var detail = event.detail || {};
      if (detail.cart) updateCartCount(detail.cart.item_count);
      if (detail.sections) this.applySections({ sections: detail.sections, item_count: detail.cart.item_count });
    }

    refresh() {
      fetch(GS.config.routes.root + '?section_id=cart-drawer')
        .then(function (r) {
          return r.text();
        })
        .then((html) => {
          var wasOpen = this.hasAttribute('open');
          replaceFrom(html, '.overlay__panel', this.querySelector('.overlay__panel'));
          if (wasOpen) this.setAttribute('open', '');
        })
        .catch(function () {
          /* Leave the existing markup in place rather than blanking the drawer. */
        });
    }

    open(opener) {
      if (super.open) super.open(opener);
      GS.analytics.track('view_cart', { source: 'drawer' });
    }
  }

  if (!customElements.get('gs-cart-drawer')) {
    customElements.define('gs-cart-drawer', GsCartDrawer);
  }

  /* ------------------------------------------------------------------ *
   * <gs-cart-page> — same behaviour, rendered inline.
   * ------------------------------------------------------------------ */

  class GsCartPage extends HTMLElement {
    connectedCallback() {
      this.addEventListener('gs:quantity:change', (event) => {
        var input = event.target.querySelector('[data-cart-quantity]');
        if (!input) return;
        this.changeLine(input.dataset.lineKey, event.detail.value, event.target);
      });

      this.addEventListener('click', (event) => {
        var remove = event.target.closest('[data-cart-remove]');
        if (!remove) return;
        event.preventDefault();
        this.changeLine(remove.dataset.lineKey, 0, remove);
      });
    }

    changeLine(key, quantity, scope) {
      if (!key) return;
      var item = scope ? scope.closest('[data-cart-item]') : null;
      if (item) item.setAttribute('aria-busy', 'true');

      GS.cart
        .change({ id: key, quantity: quantity }, ['main-cart', 'cart-icon'])
        .then((cartState) => {
          updateCartCount(cartState.item_count);
          if (quantity === 0) {
            GS.analytics.track('remove_from_cart', { line_key: key });
          }
          if (cartState.sections && cartState.sections['main-cart']) {
            var host = document.getElementById('MainCart');
            replaceFrom(cartState.sections['main-cart'], '#MainCart', host);
          } else {
            window.location.reload();
          }
        })
        .catch((error) => {
          GS.announce(error.message || GS.strings.quantityError);
          var target = item && item.querySelector('[data-line-error]');
          if (target) {
            target.textContent = error.message || GS.strings.quantityError;
            target.hidden = false;
          }
        })
        .finally(() => {
          if (item) item.removeAttribute('aria-busy');
        });
    }
  }

  if (!customElements.get('gs-cart-page')) {
    customElements.define('gs-cart-page', GsCartPage);
  }

  /* ------------------------------------------------------------------ *
   * <gs-cart-recommendations> — lazy, and silent when there is nothing.
   * ------------------------------------------------------------------ */

  class GsCartRecommendations extends HTMLElement {
    connectedCallback() {
      var url = this.dataset.url;
      if (!url) return;

      fetch(url)
        .then(function (r) {
          return r.text();
        })
        .then((html) => {
          var parsed = new DOMParser().parseFromString(html, 'text/html');
          var inner = parsed.querySelector('.cart-recommendations__inner');
          var target = this.querySelector('[data-recommendations-target]');
          if (inner && target) {
            target.innerHTML = inner.outerHTML;
          } else {
            this.hidden = true;
          }
        })
        .catch(() => {
          this.hidden = true;
        });
    }
  }

  if (!customElements.get('gs-cart-recommendations')) {
    customElements.define('gs-cart-recommendations', GsCartRecommendations);
  }
})();

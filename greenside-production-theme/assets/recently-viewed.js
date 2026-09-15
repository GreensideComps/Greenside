/**
 * Recently viewed competitions.
 *
 * Reads handles from localStorage (written by theme.js), then asks Shopify to
 * render a real competition card for each one. Prices and availability are
 * therefore always current, and no product data is ever cached client-side.
 */
(function () {
  'use strict';

  var GS = window.Greenside;
  if (!GS) return;

  class GsRecentlyViewed extends HTMLElement {
    connectedCallback() {
      var list = this.querySelector('[data-recently-viewed-list]');
      if (!list) return;

      var limit = Number(this.dataset.limit) || 4;
      var currentHandle = (readPageMeta().product || {}).handle;

      var handles = GS.recentlyViewed
        .read()
        .filter(function (handle) {
          return handle && handle !== currentHandle;
        })
        .slice(0, limit);

      if (!handles.length) return;

      Promise.all(handles.map(fetchCard))
        .then((cards) => {
          var rendered = cards.filter(Boolean);
          if (!rendered.length) return;

          rendered.forEach(function (html) {
            var item = document.createElement('li');
            item.innerHTML = html;
            list.appendChild(item);
          });

          this.hidden = false;
        })
        .catch(function () {
          /* Leave the section hidden rather than showing a broken rail. */
        });
    }
  }

  function readPageMeta() {
    var el = document.getElementById('GsPageMeta');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent);
    } catch (error) {
      return {};
    }
  }

  function fetchCard(handle) {
    return fetch(GS.config.routes.root + 'products/' + handle + '?section_id=card-product')
      .then(function (response) {
        if (!response.ok) throw new Error('Not found');
        return response.text();
      })
      .then(function (html) {
        var parsed = new DOMParser().parseFromString(html, 'text/html');
        var card = parsed.querySelector('[data-competition-card]');
        return card ? card.outerHTML : null;
      })
      .catch(function () {
        // A competition can be deleted or unpublished between visits.
        return null;
      });
  }

  if (!customElements.get('gs-recently-viewed')) {
    customElements.define('gs-recently-viewed', GsRecentlyViewed);
  }

  /* ------------------------------------------------------------------ *
   * <gs-recommendations> — lazy loads once it approaches the viewport.
   * ------------------------------------------------------------------ */

  class GsRecommendations extends HTMLElement {
    connectedCallback() {
      var url = this.dataset.url;
      if (!url) return;

      var load = () => {
        fetch(url)
          .then(function (response) {
            return response.text();
          })
          .then((html) => {
            var parsed = new DOMParser().parseFromString(html, 'text/html');
            var replacement = parsed.querySelector('gs-recommendations');
            if (replacement && replacement.innerHTML.trim()) {
              this.innerHTML = replacement.innerHTML;
            } else {
              this.hidden = true;
            }
          })
          .catch(() => {
            this.hidden = true;
          });
      };

      if ('IntersectionObserver' in window) {
        var observer = new IntersectionObserver(
          function (entries, obs) {
            if (!entries[0].isIntersecting) return;
            obs.disconnect();
            load();
          },
          { rootMargin: '300px' }
        );
        observer.observe(this);
      } else {
        load();
      }
    }
  }

  if (!customElements.get('gs-recommendations')) {
    customElements.define('gs-recommendations', GsRecommendations);
  }
})();

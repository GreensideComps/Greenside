/**
 * Collection and search filtering.
 *
 * Filters, sorting and paging are all ordinary URLs. This file intercepts them
 * and swaps the results in place, pushing the same URL to history — so the back
 * button, a refresh and a shared link all behave exactly as they would without
 * JavaScript.
 */
(function () {
  'use strict';

  var GS = window.Greenside;
  if (!GS) return;

  class GsFacets extends HTMLElement {
    connectedCallback() {
      this.sectionId = this.dataset.sectionId;
      this.results = this.querySelector('[data-facets-results]');
      this.countTarget = this.querySelector('[data-facets-count]');
      this.drawer = document.getElementById('FacetDrawer');
      this.form = this.drawer ? this.drawer.querySelector('[data-facets-form]') : null;

      this.debouncedApply = GS.utils.debounce(this.applyForm.bind(this), 350);

      this.bindSort();
      this.bindForm();
      this.bindLinks();

      window.addEventListener('popstate', this.onPopState.bind(this));
    }

    bindSort() {
      var select = this.querySelector('[data-sort-select]');
      if (!select) return;
      select.addEventListener('change', () => {
        var url = new URL(window.location.href);
        url.searchParams.set('sort_by', select.value);
        url.searchParams.delete('page');
        GS.analytics.track('filter', { type: 'sort', value: select.value });
        this.load(url.toString());
      });
    }

    bindForm() {
      if (!this.form) return;

      this.form.addEventListener('change', (event) => {
        if (event.target.type === 'number') {
          // Price fields fire on every keystroke — wait for a pause.
          this.debouncedApply();
        } else {
          this.applyForm(event.target);
        }
      });

      this.form.addEventListener('input', (event) => {
        if (event.target.type === 'number') this.debouncedApply();
      });

      // Submitting with Enter should not reload the page.
      this.form.addEventListener('submit', (event) => {
        event.preventDefault();
        this.applyForm();
      });
    }

    bindLinks() {
      // Remove-a-filter chips and pagination links.
      document.addEventListener('click', (event) => {
        var link = event.target.closest('[data-facet-remove], [data-pagination] a');
        if (!link) return;
        if (!this.contains(link) && !document.querySelector('[data-active-facets]')) return;
        event.preventDefault();
        GS.analytics.track('filter', { type: 'remove' });
        this.load(link.href);
      });

      var clear = this.drawer ? this.drawer.querySelector('[data-facets-clear]') : null;
      if (clear) {
        clear.addEventListener('click', (event) => {
          event.preventDefault();
          GS.analytics.track('filter', { type: 'clear_all' });
          this.load(clear.href);
        });
      }
    }

    applyForm(changed) {
      if (!this.form) return;

      var formData = new FormData(this.form);
      var params = new URLSearchParams();

      formData.forEach(function (value, key) {
        if (value === '' || value == null) return;
        params.append(key, value);
      });

      var sortSelect = this.querySelector('[data-sort-select]');
      if (sortSelect && sortSelect.value) params.set('sort_by', sortSelect.value);

      var base = window.location.pathname;
      var url = params.toString() ? base + '?' + params.toString() : base;

      if (changed && changed.name) {
        GS.analytics.track('filter', {
          type: 'facet',
          name: changed.name,
          value: changed.value,
          checked: changed.checked
        });
      }

      this.load(url);
    }

    load(url) {
      this.setLoading(true);

      var fetchUrl = new URL(url, window.location.origin);
      fetchUrl.searchParams.set('section_id', this.sectionId);

      fetch(fetchUrl.toString())
        .then(function (response) {
          if (!response.ok) throw new Error('Filter request failed');
          return response.text();
        })
        .then((html) => {
          this.render(html);
          window.history.pushState({ facets: true }, '', url);
          this.announceCount();
        })
        .catch(() => {
          // Fall back to a normal navigation so the customer is never stuck.
          window.location.href = url;
        })
        .finally(() => {
          this.setLoading(false);
        });
    }

    onPopState() {
      // Re-fetch for the URL the browser just restored.
      var url = window.location.href;
      var fetchUrl = new URL(url);
      fetchUrl.searchParams.set('section_id', this.sectionId);

      this.setLoading(true);
      fetch(fetchUrl.toString())
        .then(function (response) {
          return response.text();
        })
        .then((html) => this.render(html))
        .catch(function () {
          window.location.reload();
        })
        .finally(() => this.setLoading(false));
    }

    render(html) {
      var parsed = new DOMParser().parseFromString(html, 'text/html');

      var newResults = parsed.querySelector('[data-facets-results]');
      if (newResults && this.results) this.results.innerHTML = newResults.innerHTML;

      var newCount = parsed.querySelector('[data-facets-count]');
      if (newCount && this.countTarget) this.countTarget.textContent = newCount.textContent;

      // Active filter chips.
      var currentChips = this.querySelector('[data-active-facets]');
      var newChips = parsed.querySelector('[data-active-facets]');
      if (currentChips && newChips) {
        currentChips.innerHTML = newChips.innerHTML;
      } else if (currentChips && !newChips) {
        currentChips.remove();
      } else if (!currentChips && newChips) {
        var bar = this.querySelector('.facets-bar');
        if (bar) bar.insertAdjacentElement('afterend', newChips);
      }

      // Drawer contents, so counts and disabled states stay accurate.
      var newDrawer = parsed.querySelector('[data-facets-form]');
      if (newDrawer && this.form) {
        var openGroups = Array.prototype.slice
          .call(this.form.querySelectorAll('details'))
          .map(function (details) {
            return details.open;
          });
        this.form.innerHTML = newDrawer.innerHTML;
        this.form.querySelectorAll('details').forEach(function (details, index) {
          if (typeof openGroups[index] === 'boolean') details.open = openGroups[index];
        });
      }

      var toggleCount = parsed.querySelector('.facets-bar__count');
      var currentToggleCount = this.querySelector('.facets-bar__count');
      if (currentToggleCount && toggleCount) {
        currentToggleCount.textContent = toggleCount.textContent;
      } else if (currentToggleCount && !toggleCount) {
        currentToggleCount.remove();
      }
    }

    announceCount() {
      if (!this.countTarget) return;
      GS.announce(this.countTarget.textContent.trim());
    }

    setLoading(loading) {
      this.toggleAttribute('data-loading', loading);
      if (this.results) this.results.setAttribute('aria-busy', loading ? 'true' : 'false');
    }
  }

  if (!customElements.get('gs-facets')) {
    customElements.define('gs-facets', GsFacets);
  }
})();

/**
 * Predictive search.
 *
 * Implements the ARIA combobox pattern. Focus stays in the input at all times;
 * arrow keys move aria-activedescendant across the options so a screen reader
 * announces each result while the query remains editable.
 */
(function () {
  'use strict';

  var GS = window.Greenside;
  if (!GS) return;

  var MIN_QUERY = 2;

  class GsSearchModal extends (customElements.get('gs-overlay') || HTMLElement) {
    connectedCallback() {
      if (super.connectedCallback) super.connectedCallback();

      this.input = this.querySelector('[data-search-input]');
      this.results = this.querySelector('[data-search-results]');
      this.status = this.querySelector('[data-search-status]');
      this.clearButton = this.querySelector('[data-search-clear]');
      this.form = this.querySelector('[data-search-form]');
      this.defaultContent = this.results ? this.results.innerHTML : '';
      this.activeIndex = -1;
      this.abortController = null;

      if (!this.input) return;

      this.onInput = GS.utils.debounce(this.search.bind(this), 250);
      this.input.addEventListener('input', () => {
        this.toggleClear();
        this.onInput();
      });
      this.input.addEventListener('keydown', this.onKeydown.bind(this));

      if (this.clearButton) {
        this.clearButton.addEventListener('click', () => this.clear());
      }

      if (this.form) {
        this.form.addEventListener('submit', () => {
          var terms = this.input.value.trim();
          if (terms) GS.analytics.track('search', { search_term: terms, source: 'modal' });
        });
      }

      this.toggleClear();
    }

    toggleClear() {
      if (this.clearButton) this.clearButton.hidden = this.input.value.length === 0;
    }

    clear() {
      this.input.value = '';
      this.toggleClear();
      this.reset();
      this.input.focus();
    }

    reset() {
      if (this.results) this.results.innerHTML = this.defaultContent;
      this.input.setAttribute('aria-expanded', 'false');
      this.input.removeAttribute('aria-activedescendant');
      this.activeIndex = -1;
    }

    search() {
      var terms = this.input.value.trim();

      if (terms.length < MIN_QUERY) {
        this.reset();
        return;
      }

      // Cancel an in-flight request so results can never arrive out of order.
      if (this.abortController) this.abortController.abort();
      this.abortController = new AbortController();

      var params = new URLSearchParams({
        q: terms,
        section_id: 'predictive-search'
      });
      params.append('resources[limit]', '6');
      params.append('resources[limit_scope]', 'each');

      var types = ['product'];
      if (this.dataset.showPages !== 'false') types.push('page');
      if (this.dataset.showArticles !== 'false') types.push('article');
      params.append('resources[type]', types.join(','));
      params.append('resources[options][unavailable_products]', 'last');

      fetch(GS.config.routes.predictiveSearch + '?' + params.toString(), {
        signal: this.abortController.signal
      })
        .then(function (response) {
          if (!response.ok) throw new Error('Search failed');
          return response.text();
        })
        .then((html) => {
          this.render(html, terms);
        })
        .catch((error) => {
          if (error.name === 'AbortError') return;
          this.renderError();
        });
    }

    render(html, terms) {
      if (!this.results) return;
      var parsed = new DOMParser().parseFromString(html, 'text/html');
      var section = parsed.querySelector('#shopify-section-predictive-search') || parsed.body;

      this.results.innerHTML = section.innerHTML;

      this.options = Array.prototype.slice.call(this.results.querySelectorAll('[role="option"]'));
      this.activeIndex = -1;
      this.input.setAttribute('aria-expanded', this.options.length > 0 ? 'true' : 'false');
      this.input.removeAttribute('aria-activedescendant');

      var countEl = this.results.querySelector('[data-search-count]');
      var count = countEl ? Number(countEl.textContent) : this.options.length;

      if (this.status) {
        this.status.textContent =
          count > 0
            ? GS.strings.searchResultsAvailable.replace('{{ count }}', count)
            : GS.strings.searchNoResults;
      }

      GS.analytics.track('search', { search_term: terms, results: count, source: 'predictive' });
    }

    renderError() {
      if (!this.results) return;
      this.results.innerHTML =
        '<div class="predictive__empty"><p>' + GS.strings.networkError + '</p></div>';
      this.input.setAttribute('aria-expanded', 'false');
      if (this.status) this.status.textContent = GS.strings.networkError;
    }

    onKeydown(event) {
      if (!this.options || !this.options.length) return;

      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          this.move(1);
          break;
        case 'ArrowUp':
          event.preventDefault();
          this.move(-1);
          break;
        case 'Home':
          if (this.activeIndex > -1) {
            event.preventDefault();
            this.setActive(0);
          }
          break;
        case 'End':
          if (this.activeIndex > -1) {
            event.preventDefault();
            this.setActive(this.options.length - 1);
          }
          break;
        case 'Enter':
          if (this.activeIndex > -1) {
            event.preventDefault();
            var link = this.options[this.activeIndex].querySelector('a');
            if (link) link.click();
          }
          break;
        default:
          break;
      }
    }

    move(delta) {
      var next = this.activeIndex + delta;
      if (next < 0) next = this.options.length - 1;
      if (next >= this.options.length) next = 0;
      this.setActive(next);
    }

    setActive(index) {
      this.options.forEach(function (option, i) {
        option.setAttribute('aria-selected', i === index ? 'true' : 'false');
      });
      this.activeIndex = index;
      var option = this.options[index];
      if (!option) return;
      this.input.setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({ block: 'nearest' });
    }

    close() {
      if (super.close) super.close();
      this.reset();
    }
  }

  if (!customElements.get('gs-search-modal')) {
    customElements.define('gs-search-modal', GsSearchModal);
  }
})();

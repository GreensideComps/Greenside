/**
 * Winners page "load more".
 *
 * Every winner is already in the document, so this reveals rather than fetches:
 * the full list is crawlable and works with JavaScript disabled, where all
 * winners simply render at once.
 */
(function () {
  'use strict';

  var GS = window.Greenside;

  class GsWinners extends HTMLElement {
    connectedCallback() {
      this.items = Array.prototype.slice.call(this.querySelectorAll('[data-winner-item]'));
      this.button = this.querySelector('[data-winners-more]');
      this.status = this.querySelector('[data-winners-status]');
      this.perPage = Number(this.dataset.perPage) || 12;
      this.shown = this.items.filter(function (item) {
        return !item.hidden;
      }).length;

      if (!this.button) return;
      this.button.addEventListener('click', this.showMore.bind(this));
    }

    showMore() {
      var next = this.items.slice(this.shown, this.shown + this.perPage);
      next.forEach(function (item) {
        item.hidden = false;
      });

      var firstNew = next[0];
      this.shown += next.length;

      if (this.shown >= this.items.length && this.button) {
        this.button.hidden = true;
      }

      var message = this.shown + ' of ' + this.items.length;
      if (this.status) this.status.textContent = message;
      if (GS) GS.announce(message);

      // Move focus to the first newly revealed card so keyboard users land
      // where the new content starts rather than at the top of the page.
      if (firstNew) {
        var focusable = firstNew.querySelector('a, button');
        if (focusable) focusable.focus({ preventScroll: true });
      }
    }
  }

  if (!customElements.get('gs-winners')) {
    customElements.define('gs-winners', GsWinners);
  }
})();

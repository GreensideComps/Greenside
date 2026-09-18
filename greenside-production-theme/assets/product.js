/**
 * Competition page behaviour: gallery, entry selection and live totals.
 *
 * Everything here is an enhancement. With JavaScript disabled the gallery is
 * still a scrollable list of images, the quantity field is a normal number
 * input, and the form posts to Shopify in the usual way.
 */
(function () {
  'use strict';

  var GS = window.Greenside;
  if (!GS) return;

  /* ------------------------------------------------------------------ *
   * <gs-gallery>
   *
   * Thumbnails are toggle buttons carrying aria-pressed. Arrow keys move
   * between them as a convenience; unlike a tablist every thumbnail stays in
   * the tab order, which matches the fact that every slide is really present
   * in the scroller rather than swapped in and out.
   * ------------------------------------------------------------------ */

  class GsGallery extends HTMLElement {
    connectedCallback() {
      this.track = this.querySelector('[data-gallery-track]');
      this.slides = Array.prototype.slice.call(this.querySelectorAll('[data-gallery-slide]'));
      this.thumbs = Array.prototype.slice.call(this.querySelectorAll('[data-gallery-thumb]'));
      this.prevButton = this.querySelector('[data-gallery-prev]');
      this.nextButton = this.querySelector('[data-gallery-next]');

      if (!this.track || this.slides.length < 2) {
        if (this.prevButton) this.prevButton.hidden = true;
        if (this.nextButton) this.nextButton.hidden = true;
        return;
      }

      this.index = 0;

      this.thumbs.forEach((thumb, i) => {
        thumb.addEventListener('click', () => this.select(i, true));
        thumb.addEventListener('keydown', (event) => this.onThumbKeydown(event, i));
      });

      if (this.prevButton) this.prevButton.addEventListener('click', () => this.step(-1));
      if (this.nextButton) this.nextButton.addEventListener('click', () => this.step(1));

      // Keep the thumbnails in step when the rail is swiped directly.
      if ('IntersectionObserver' in window) {
        this.observer = new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (!entry.isIntersecting) return;
              var i = this.slides.indexOf(entry.target);
              if (i > -1) this.setActive(i);
            });
          },
          { root: this.track, threshold: 0.6 }
        );
        this.slides.forEach((slide) => this.observer.observe(slide));
      }

      this.updateButtons();
    }

    disconnectedCallback() {
      if (this.observer) this.observer.disconnect();
    }

    onThumbKeydown(event, index) {
      var next = null;
      if (event.key === 'ArrowRight') next = (index + 1) % this.thumbs.length;
      if (event.key === 'ArrowLeft') next = (index - 1 + this.thumbs.length) % this.thumbs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = this.thumbs.length - 1;
      if (next === null) return;
      event.preventDefault();
      this.select(next, true);
      this.thumbs[next].focus();
    }

    step(direction) {
      var next = Math.min(Math.max(this.index + direction, 0), this.slides.length - 1);
      this.select(next, true);
    }

    select(index, scroll) {
      this.setActive(index);
      if (scroll && this.slides[index]) {
        this.slides[index].scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
      }
    }

    setActive(index) {
      this.index = index;
      this.thumbs.forEach(function (thumb, i) {
        thumb.setAttribute('aria-pressed', i === index ? 'true' : 'false');
      });
      this.updateButtons();
    }

    updateButtons() {
      if (this.prevButton) this.prevButton.disabled = this.index === 0;
      if (this.nextButton) this.nextButton.disabled = this.index === this.slides.length - 1;
    }

    /** Jump to the image belonging to a variant, used by the bundle picker. */
    showMedia(mediaId) {
      var index = this.slides.findIndex(function (slide) {
        return slide.dataset.mediaId === String(mediaId);
      });
      if (index > -1) this.select(index, true);
    }
  }

  if (!customElements.get('gs-gallery')) {
    customElements.define('gs-gallery', GsGallery);
  }

  /* ------------------------------------------------------------------ *
   * <gs-entry-selector> logic, attached to the form.
   *
   * Keeps the running total honest: it is always quantity multiplied by the
   * real Shopify price of the selected variant, formatted with the shop's own
   * money format.
   * ------------------------------------------------------------------ */

  function initEntrySelector(root) {
    var selector = root.querySelector('[data-entry-selector]');
    if (!selector) return;

    var totalEl = selector.querySelector('[data-entry-total]');
    var quantityInput = selector.querySelector('[data-entry-quantity]');
    var presets = Array.prototype.slice.call(selector.querySelectorAll('[data-entry-preset]'));
    var bundleInputs = Array.prototype.slice.call(selector.querySelectorAll('.entry-bundle__input'));

    function unitPrice() {
      if (quantityInput) return Number(quantityInput.dataset.unitPrice || 0);
      var checked = bundleInputs.find(function (input) {
        return input.checked;
      });
      return checked ? Number(checked.dataset.variantPrice || 0) : 0;
    }

    function currentQuantity() {
      if (quantityInput) return Number(quantityInput.value || 1);
      return 1;
    }

    function entriesSelected() {
      if (quantityInput) return currentQuantity();
      var checked = bundleInputs.find(function (input) {
        return input.checked;
      });
      return checked ? Number(checked.dataset.variantEntries || 1) : 1;
    }

    function renderTotal() {
      if (!totalEl) return;
      totalEl.textContent = GS.money(unitPrice() * currentQuantity());
    }

    function syncPresets() {
      if (!presets.length || !quantityInput) return;
      var value = currentQuantity();
      presets.forEach(function (button) {
        var matches = Number(button.dataset.entryPreset) === value;
        button.setAttribute('aria-pressed', matches ? 'true' : 'false');
      });
    }

    presets.forEach(function (button) {
      button.addEventListener('click', function () {
        if (!quantityInput) return;
        var amount = Number(button.dataset.entryPreset);
        var wrapper = quantityInput.closest('gs-quantity');
        if (wrapper && typeof wrapper.setValue === 'function') {
          wrapper.setValue(amount);
        } else {
          quantityInput.value = String(amount);
        }
        syncPresets();
        renderTotal();
        GS.analytics.track('entry_quantity_selected', {
          quantity: amount,
          method: 'preset',
          product_id: root.dataset.productId
        });
      });
    });

    selector.addEventListener('gs:quantity:change', function (event) {
      syncPresets();
      renderTotal();
      GS.analytics.track('entry_quantity_selected', {
        quantity: event.detail.value,
        method: 'stepper',
        product_id: root.dataset.productId
      });
    });

    bundleInputs.forEach(function (input) {
      input.addEventListener('change', function () {
        renderTotal();
        GS.analytics.track('entry_quantity_selected', {
          quantity: entriesSelected(),
          variant_id: Number(input.value),
          method: 'bundle',
          product_id: root.dataset.productId
        });

        // Show the image belonging to the chosen bundle, if it has its own.
        var gallery = document.querySelector('gs-gallery');
        var mediaId = input.dataset.variantMedia;
        if (gallery && mediaId && typeof gallery.showMedia === 'function') {
          gallery.showMedia(mediaId);
        }
      });
    });

    renderTotal();
    syncPresets();
  }

  /* ------------------------------------------------------------------ *
   * Skill question validation.
   *
   * The radio group is required, but a native required radio produces a
   * browser bubble that is easy to miss on mobile, so the message is rendered
   * inline and announced instead.
   * ------------------------------------------------------------------ */

  function initSkillQuestion(root) {
    var form = root.querySelector('form');
    if (!form) return;

    var answers = Array.prototype.slice.call(form.querySelectorAll('[data-skill-answer]'));
    if (!answers.length) return;

    var error = form.querySelector('[data-skill-error]');
    var errorText = form.querySelector('[data-skill-error-text]');
    var message = form.querySelector('.skill-question__legend');
    var prompt = 'Please choose an answer to continue.';

    form.addEventListener(
      'submit',
      function (event) {
        var chosen = answers.some(function (input) {
          return input.checked;
        });
        if (chosen) {
          if (error) error.hidden = true;
          return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
        if (errorText) errorText.textContent = prompt;
        if (error) error.hidden = false;
        GS.announce(prompt);
        answers[0].focus();
        if (message) message.scrollIntoView({ behavior: 'smooth', block: 'center' });
      },
      // Capture so this runs before the add-to-cart handler.
      true
    );

    answers.forEach(function (input) {
      input.addEventListener('change', function () {
        if (error) error.hidden = true;
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Live entry progress.
   *
   * Only runs when a merchant has configured an endpoint. Without one the
   * progress bars keep showing their metafield values, which are accurate at
   * page render. Nothing is ever invented client-side.
   * ------------------------------------------------------------------ */

  function initLiveProgress() {
    var endpoint = GS.config.entryProgressEndpoint;
    if (!endpoint) return;

    var bars = Array.prototype.slice.call(document.querySelectorAll('[data-entry-progress]'));
    if (!bars.length) return;

    var ids = bars
      .map(function (bar) {
        return bar.dataset.productId;
      })
      .filter(Boolean);
    if (!ids.length) return;

    var url = endpoint + (endpoint.indexOf('?') > -1 ? '&' : '?') + 'ids=' + ids.join(',');

    fetch(url, { headers: { Accept: 'application/json' } })
      .then(function (response) {
        if (!response.ok) throw new Error('Progress unavailable');
        return response.json();
      })
      .then(function (data) {
        // Expected shape: { "<product_id>": { "sold": 6420, "total": 10000 } }
        bars.forEach(function (bar) {
          var entry = data && data[bar.dataset.productId];
          if (!entry || typeof entry.sold !== 'number' || typeof entry.total !== 'number') return;
          if (entry.total <= 0) return;
          updateProgressBar(bar, entry.sold, entry.total);
        });
      })
      .catch(function () {
        /* Keep the rendered metafield values: they are still correct. */
      });
  }

  function updateProgressBar(bar, sold, total) {
    var safeSold = Math.max(0, Math.min(sold, total));
    var percent = Math.round((safeSold / total) * 100);

    var fill = bar.querySelector('[data-progress-fill]');
    if (fill) fill.style.width = percent + '%';

    var track = bar.querySelector('[role="progressbar"]');
    if (track) {
      track.setAttribute('aria-valuenow', String(safeSold));
      track.setAttribute('aria-valuemax', String(total));
    }

    var count = bar.querySelector('[data-progress-count]');
    if (count) {
      count.textContent = count.textContent
        .replace(/[\d,]+/, safeSold.toLocaleString())
        .replace(/([\d,]+)(?!.*[\d,])/, total.toLocaleString());
    }

    var percentEl = bar.querySelector('[data-progress-percent]');
    if (percentEl) percentEl.textContent = percentEl.textContent.replace(/\d+/, String(percent));

    bar.dataset.entriesSold = String(safeSold);
    bar.dataset.entriesTotal = String(total);
  }

  /* ------------------------------------------------------------------ *
   * Native share sheet, where the browser has one.
   * ------------------------------------------------------------------ */

  function initShare() {
    document.querySelectorAll('[data-share]').forEach(function (share) {
      if (!navigator.share) return;
      var button = share.querySelector('[data-share-native]');
      var links = share.querySelector('[data-share-links]');
      if (!button) return;

      button.hidden = false;
      if (links) links.hidden = true;

      button.addEventListener('click', function () {
        navigator
          .share({ title: share.dataset.title, url: share.dataset.url })
          .then(function () {
            GS.analytics.track('referral_share', { channel: 'native' });
          })
          .catch(function () {
            // Cancelled, or unavailable — restore the explicit links.
            if (links) links.hidden = false;
            button.hidden = true;
          });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Boot
   * ------------------------------------------------------------------ */

  function boot() {
    document.querySelectorAll('gs-add-to-cart').forEach(function (root) {
      initEntrySelector(root);
      initSkillQuestion(root);
    });
    initLiveProgress();
    initShare();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // Re-run when a section is re-rendered in the theme editor.
  document.addEventListener('shopify:section:load', boot);
})();

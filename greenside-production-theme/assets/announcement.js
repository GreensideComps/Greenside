/**
 * Announcement bar rotation.
 *
 * Rotation is an enhancement: with one message, or with reduced motion, every
 * message is simply present and the interval never starts.
 */
(function () {
  'use strict';

  class GsAnnouncement extends HTMLElement {
    connectedCallback() {
      this.slides = Array.prototype.slice.call(this.querySelectorAll('[data-announcement-slide]'));
      if (this.slides.length < 2) return;

      var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
      if (reduced.matches) {
        this.showAll();
        return;
      }

      this.index = 0;
      this.interval = Number(this.dataset.interval) || 6000;

      this.addEventListener('mouseenter', () => this.stop());
      this.addEventListener('mouseleave', () => this.start());
      this.addEventListener('focusin', () => this.stop());
      this.addEventListener('focusout', () => this.start());

      // Pause while the tab is hidden so nothing animates off-screen.
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) this.stop();
        else this.start();
      });

      this.start();
    }

    disconnectedCallback() {
      this.stop();
    }

    showAll() {
      this.slides.forEach(function (slide) {
        slide.hidden = false;
        slide.removeAttribute('aria-hidden');
        slide.style.position = 'static';
      });
      this.style.display = 'flex';
      this.style.flexWrap = 'wrap';
      this.style.gap = '1rem';
      this.style.justifyContent = 'center';
    }

    start() {
      this.stop();
      this.timer = window.setInterval(this.next.bind(this), this.interval);
    }

    stop() {
      if (this.timer) window.clearInterval(this.timer);
      this.timer = null;
    }

    next() {
      var previous = this.slides[this.index];
      this.index = (this.index + 1) % this.slides.length;
      var current = this.slides[this.index];

      previous.hidden = true;
      previous.setAttribute('aria-hidden', 'true');
      current.hidden = false;
      current.removeAttribute('aria-hidden');
    }
  }

  if (!customElements.get('gs-announcement')) {
    customElements.define('gs-announcement', GsAnnouncement);
  }
})();

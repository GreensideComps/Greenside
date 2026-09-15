/**
 * Header behaviour: desktop dropdowns and the sticky shadow.
 *
 * Dropdowns follow the WAI-ARIA disclosure pattern rather than a menu pattern,
 * because each panel is a list of ordinary links. That means:
 *   - the trigger is a <button> with aria-expanded
 *   - Escape closes and returns focus to the trigger
 *   - moving focus out of the item closes it
 *   - hover opens on pointer devices but never traps keyboard users
 */
(function () {
  'use strict';

  class GsHeader extends HTMLElement {
    connectedCallback() {
      this.items = Array.prototype.slice.call(this.querySelectorAll('[data-nav-item]'));
      this.items.forEach((item) => this.bindItem(item));

      document.addEventListener('click', this.onDocumentClick.bind(this));

      if (this.hasAttribute('data-sticky')) {
        this.bindSticky();
      }

      this.measure();
      window.addEventListener('resize', this.measure.bind(this), { passive: true });
    }

    /** Publish the real header height so CSS scroll-padding can use it. */
    measure() {
      var height = this.getBoundingClientRect().height;
      if (height > 0) {
        document.documentElement.style.setProperty('--header-height', Math.round(height) + 'px');
      }
    }

    bindItem(item) {
      var trigger = item.querySelector('[data-nav-trigger]');
      var panel = item.querySelector('[data-nav-panel]');
      if (!trigger || !panel) return;

      trigger.addEventListener('click', (event) => {
        event.preventDefault();
        this.toggle(item, item.dataset.open !== 'true');
      });

      item.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && item.dataset.open === 'true') {
          event.stopPropagation();
          this.toggle(item, false);
          trigger.focus();
        }
        if (event.key === 'ArrowDown' && document.activeElement === trigger) {
          event.preventDefault();
          this.toggle(item, true);
          var first = panel.querySelector('a');
          if (first) first.focus();
        }
      });

      // Closing on focusout keeps tabbing linear: tab past the last link and
      // the panel closes behind you.
      item.addEventListener('focusout', () => {
        window.requestAnimationFrame(() => {
          if (!item.contains(document.activeElement)) this.toggle(item, false);
        });
      });

      if (window.matchMedia('(hover: hover) and (min-width: 990px)').matches) {
        item.addEventListener('mouseenter', () => this.toggle(item, true));
        item.addEventListener('mouseleave', () => {
          if (!item.contains(document.activeElement)) this.toggle(item, false);
        });
      }
    }

    toggle(item, open) {
      var trigger = item.querySelector('[data-nav-trigger]');
      if (open) {
        this.items.forEach((other) => {
          if (other !== item) this.toggle(other, false);
        });
      }
      item.dataset.open = open ? 'true' : 'false';
      if (trigger) trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    onDocumentClick(event) {
      if (this.contains(event.target)) return;
      this.items.forEach((item) => this.toggle(item, false));
    }

    bindSticky() {
      var onScroll = () => {
        this.classList.toggle('header-wrapper--scrolled', window.scrollY > 8);
      };
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();
    }
  }

  if (!customElements.get('gs-header')) {
    customElements.define('gs-header', GsHeader);
  }
})();

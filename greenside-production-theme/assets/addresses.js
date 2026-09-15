/**
 * Address book.
 *
 * Two jobs: show and hide the per-address edit form, and keep the province
 * select in step with the chosen country using the province data Shopify
 * attaches to each country option.
 */
(function () {
  'use strict';

  function initToggles() {
    document.querySelectorAll('[data-address-toggle]').forEach(function (button) {
      button.addEventListener('click', function () {
        var panel = document.getElementById(button.dataset.addressToggle);
        if (!panel) return;

        var willOpen = panel.hidden;
        panel.hidden = !willOpen;

        // Only the trigger that owns aria-controls reflects the state.
        document
          .querySelectorAll('[data-address-toggle="' + button.dataset.addressToggle + '"]')
          .forEach(function (trigger) {
            if (trigger.hasAttribute('aria-controls')) {
              trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            }
          });

        if (willOpen) {
          var field = panel.querySelector('input, select');
          if (field) field.focus();
        }
      });
    });
  }

  function initDeleteConfirm() {
    document.querySelectorAll('[data-confirm-message]').forEach(function (button) {
      button.addEventListener('click', function (event) {
        if (!window.confirm(button.dataset.confirmMessage)) {
          event.preventDefault();
        }
      });
    });
  }

  function initCountryProvince() {
    document.querySelectorAll('[data-address-country]').forEach(function (countrySelect) {
      var form = countrySelect.closest('form');
      if (!form) return;

      var provinceSelect = form.querySelector('[data-address-province]');
      var wrapper = form.querySelector('[data-address-province-wrapper]');
      if (!provinceSelect || !wrapper) return;

      function sync() {
        var option = countrySelect.options[countrySelect.selectedIndex];
        var raw = option ? option.getAttribute('data-provinces') : null;
        var provinces = [];

        try {
          provinces = raw ? JSON.parse(raw) : [];
        } catch (error) {
          provinces = [];
        }

        provinceSelect.innerHTML = '';

        if (!provinces.length) {
          wrapper.hidden = true;
          // Disabled so an empty province is not submitted.
          provinceSelect.disabled = true;
          return;
        }

        wrapper.hidden = false;
        provinceSelect.disabled = false;

        var preferred = provinceSelect.dataset.default;
        provinces.forEach(function (pair) {
          var opt = document.createElement('option');
          opt.value = pair[0];
          opt.textContent = pair[1];
          if (preferred && preferred === pair[0]) opt.selected = true;
          provinceSelect.appendChild(opt);
        });
      }

      // Restore the saved country before the first sync.
      var savedCountry = countrySelect.dataset.default;
      if (savedCountry) countrySelect.value = savedCountry;

      countrySelect.addEventListener('change', sync);
      sync();
    });
  }

  function init() {
    initToggles();
    initDeleteConfirm();
    initCountryProvince();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

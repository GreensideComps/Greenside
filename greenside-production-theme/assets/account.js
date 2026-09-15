/**
 * Login page: toggling between sign-in and password recovery.
 *
 * Shopify links to #recover, so a customer arriving from a reset email lands on
 * the right panel. Focus moves with the panel so the change is announced.
 */
(function () {
  'use strict';

  function init() {
    var login = document.getElementById('LoginPanel');
    var recover = document.getElementById('RecoverPassword');
    if (!login || !recover) return;

    function show(which) {
      var showRecover = which === 'recover';
      recover.style.display = showRecover ? 'block' : 'none';
      login.style.display = showRecover ? 'none' : 'block';

      var target = (showRecover ? recover : login).querySelector('input, button');
      if (target) target.focus();
    }

    document.querySelectorAll('[data-show-recover]').forEach(function (button) {
      button.addEventListener('click', function () {
        show('recover');
        window.history.replaceState(null, '', '#recover');
      });
    });

    document.querySelectorAll('[data-show-login]').forEach(function (button) {
      button.addEventListener('click', function () {
        show('login');
        window.history.replaceState(null, '', window.location.pathname);
      });
    });

    // Arriving at #recover, or bouncing back after a failed reset attempt.
    if (window.location.hash === '#recover' || recover.querySelector('.form-status--success')) {
      recover.style.display = 'block';
      login.style.display = 'none';
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

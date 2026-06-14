// Settings page: save form and test webhook.

(function () {
  const form = document.getElementById('settings-form');
  const saveResult = document.getElementById('save-result');

  // --- reorderable lists (nav tabs + upcoming tabs) ---
  // opts.hiddenInputId, if given, is kept in sync with the unchecked rows'
  // keys (so a list can carry both an order and a hidden set).
  function wireReorder(listId, orderInputId, opts) {
    const list = document.getElementById(listId);
    const orderInput = document.getElementById(orderInputId);
    if (!list || !orderInput) return;
    opts = opts || {};
    const hiddenInput = opts.hiddenInputId ? document.getElementById(opts.hiddenInputId) : null;

    function sync() {
      const lis = Array.from(list.querySelectorAll('li'));
      orderInput.value = lis.map(function (li) { return li.getAttribute('data-key'); }).join(',');
      if (hiddenInput) {
        hiddenInput.value = lis.filter(function (li) {
          const cb = li.querySelector('input[type="checkbox"]');
          return cb && !cb.checked;
        }).map(function (li) { return li.getAttribute('data-key'); }).join(',');
      }
    }

    list.addEventListener('click', function (e) {
      const li = e.target.closest('li');
      if (!li) return;
      if (e.target.classList.contains('nav-up') && li.previousElementSibling) {
        list.insertBefore(li, li.previousElementSibling);
        sync();
      } else if (e.target.classList.contains('nav-down') && li.nextElementSibling) {
        list.insertBefore(li.nextElementSibling, li);
        sync();
      }
    });
    if (hiddenInput) {
      list.addEventListener('change', function (e) {
        if (e.target.type === 'checkbox') sync();
      });
    }
    sync();
  }

  wireReorder('nav-order-list', 'nav_order');
  wireReorder('upcoming-order-list', 'upcoming_tabs_order', { hiddenInputId: 'upcoming_tabs_hidden' });

  // Collect form values, joining repeated keys (e.g. the monitor-type
  // checkboxes) into a comma string the API understands.
  function collect() {
    const data = {};
    new FormData(form).forEach(function (value, key) {
      data[key] = key in data ? data[key] + ',' + value : value;
    });
    // Ensure checkbox groups are always sent, even when fully unchecked.
    if (!('default_monitor_types' in data)) data.default_monitor_types = '';
    if (!('discography_autohide' in data)) data.discography_autohide = '';
    return data;
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    const data = collect();
    saveResult.textContent = 'Saving...';
    SMT.postJSON('/api/settings', data).then(function () {
      saveResult.textContent = 'Saved.';
      setTimeout(function () { saveResult.textContent = ''; }, 2500);
    }).catch(function () {
      saveResult.textContent = 'Save failed.';
    });
  });

  const testBtn = document.getElementById('btn-test-webhook');
  const webhookResult = document.getElementById('webhook-result');
  testBtn.addEventListener('click', function () {
    webhookResult.textContent = 'Sending...';
    // Save first so the test uses current values.
    SMT.postJSON('/api/settings', collect()).then(function () {
      return SMT.postJSON('/api/webhook/test', {});
    }).then(function (r) {
      webhookResult.textContent = r.ok ? ('OK (' + r.message + ')') : ('Failed: ' + r.message);
    }).catch(function () {
      webhookResult.textContent = 'Failed.';
    });
  });
})();

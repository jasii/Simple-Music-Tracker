// Settings page: save form and test webhook.

(function () {
  const form = document.getElementById('settings-form');
  const saveResult = document.getElementById('save-result');

  // --- nav order reordering ---
  const navList = document.getElementById('nav-order-list');
  const navOrderInput = document.getElementById('nav_order');

  function syncNavOrder() {
    const keys = Array.from(navList.querySelectorAll('li'))
      .map(function (li) { return li.getAttribute('data-key'); });
    navOrderInput.value = keys.join(',');
  }

  if (navList) {
    navList.addEventListener('click', function (e) {
      const li = e.target.closest('li');
      if (!li) return;
      if (e.target.classList.contains('nav-up') && li.previousElementSibling) {
        navList.insertBefore(li, li.previousElementSibling);
        syncNavOrder();
      } else if (e.target.classList.contains('nav-down') && li.nextElementSibling) {
        navList.insertBefore(li.nextElementSibling, li);
        syncNavOrder();
      }
    });
    syncNavOrder();
  }

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

// Settings page: save form and test webhook.

(function () {
  const form = document.getElementById('settings-form');
  const saveResult = document.getElementById('save-result');

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    const data = {};
    new FormData(form).forEach(function (value, key) { data[key] = value; });
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
    const data = {};
    new FormData(form).forEach(function (value, key) { data[key] = value; });
    SMT.postJSON('/api/settings', data).then(function () {
      return SMT.postJSON('/api/webhook/test', {});
    }).then(function (r) {
      webhookResult.textContent = r.ok ? ('OK (' + r.message + ')') : ('Failed: ' + r.message);
    }).catch(function () {
      webhookResult.textContent = 'Failed.';
    });
  });
})();

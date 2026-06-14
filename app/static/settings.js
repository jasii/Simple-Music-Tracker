// Settings page: save form and test webhook.

(function () {
  const form = document.getElementById('settings-form');
  const saveResult = document.getElementById('save-result');

  // --- reorderable list (nav tabs) with drag-and-drop + Up/Down ---
  // The first item is highlighted as the home page.
  function wireReorder(listId, orderInputId) {
    const list = document.getElementById(listId);
    const orderInput = document.getElementById(orderInputId);
    if (!list || !orderInput) return;

    function sync() {
      const lis = Array.from(list.querySelectorAll('li'));
      orderInput.value = lis.map(function (li) { return li.getAttribute('data-key'); }).join(',');
      lis.forEach(function (li, i) { li.classList.toggle('is-home', i === 0); });
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

    // Drag and drop.
    let dragging = null;
    list.addEventListener('dragstart', function (e) {
      dragging = e.target.closest('li');
      if (dragging) { dragging.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; }
    });
    list.addEventListener('dragend', function () {
      if (dragging) dragging.classList.remove('dragging');
      dragging = null;
      sync();
    });
    list.addEventListener('dragover', function (e) {
      e.preventDefault();
      if (!dragging) return;
      const after = Array.from(list.querySelectorAll('li:not(.dragging)')).find(function (li) {
        const box = li.getBoundingClientRect();
        return e.clientY < box.top + box.height / 2;
      });
      if (after) list.insertBefore(dragging, after);
      else list.appendChild(dragging);
    });

    sync();
  }

  wireReorder('nav-order-list', 'nav_order');

  // Show the webhook lead-time fields only for the "before release" trigger.
  const trigger = document.getElementById('webhook_trigger');
  const leadFields = document.getElementById('webhook-lead-fields');
  if (trigger && leadFields) {
    const syncLead = function () { leadFields.style.display = trigger.value === 'before_release' ? '' : 'none'; };
    trigger.addEventListener('change', syncLead);
    syncLead();
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
    // A lone unchecked checkbox is absent from FormData; send false explicitly.
    if (!('prefer_album_artist' in data)) data.prefer_album_artist = 'false';
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

  // --- test Last.fm API key / cookie (save current values first) ---
  function wireHealthCheck(btnId, resultId, endpoint) {
    const btn = document.getElementById(btnId);
    const result = document.getElementById(resultId);
    if (!btn) return;
    btn.addEventListener('click', function () {
      result.textContent = 'Checking...';
      SMT.postJSON('/api/settings', collect())
        .then(function () { return SMT.postJSON(endpoint, {}); })
        .then(function (r) { result.textContent = (r.ok ? 'OK - ' : 'Failed - ') + (r.message || ''); })
        .catch(function () { result.textContent = 'Check failed.'; });
    });
  }
  wireHealthCheck('btn-test-key', 'key-result', '/api/health/lastfm-key');
  wireHealthCheck('btn-test-cookie', 'cookie-result', '/api/health/lastfm-cookie');

  // --- import / restore from a backup file ---
  const importBtn = document.getElementById('btn-import');
  const importFile = document.getElementById('import-file');
  const importResult = document.getElementById('import-result');
  if (importBtn) {
    importBtn.addEventListener('click', function () {
      const file = importFile.files && importFile.files[0];
      if (!file) { importResult.textContent = 'Choose a backup file first.'; return; }
      if (!window.confirm('Importing replaces ALL current settings and data with this backup. Continue?')) return;
      importResult.textContent = 'Importing...';
      const fd = new FormData();
      fd.append('file', file);
      fetch('/api/import', { method: 'POST', body: fd })
        .then(function (res) { return res.json(); })
        .then(function (r) {
          if (r.error) { importResult.textContent = r.error; return; }
          const c = r.imported || {};
          importResult.textContent = 'Imported ' + (c.artists || 0) + ' artists, ' +
            (c.releases || 0) + ' releases. Reloading...';
          setTimeout(function () { window.location.reload(); }, 1000);
        })
        .catch(function () { importResult.textContent = 'Import failed.'; });
    });
  }
})();

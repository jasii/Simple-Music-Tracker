// Artists table: load, filter, per-row subscribe/notify, bulk actions,
// and scan / refresh progress polling.

(function () {
  const body = document.getElementById('artists-body');
  const search = document.getElementById('artist-search');
  const filterSel = document.getElementById('filter-subscription');
  const sortSel = document.getElementById('sort-by');
  const selectAll = document.getElementById('select-all');
  const bulkBar = document.getElementById('bulk-bar');
  const bulkCount = document.getElementById('bulk-count');
  const footer = document.getElementById('table-footer');
  const progress = document.getElementById('progress');

  let artists = [];
  const selected = new Set();

  function rowHTML(a) {
    const subChecked = a.subscription === 'subscribed' || a.subscription === 'notify';
    const notifyChecked = a.subscription === 'notify';
    return (
      '<tr data-id="' + a.id + '">' +
      '<td class="col-check"><input type="checkbox" class="row-select"' +
        (selected.has(a.id) ? ' checked' : '') + '></td>' +
      '<td class="col-name"><a href="/artist/' + a.id + '">' + SMT.esc(a.name) + '</a></td>' +
      '<td class="col-tracks">' + (a.track_count || 0) + '</td>' +
      '<td class="col-sub"><input type="checkbox" class="sub-toggle"' +
        (subChecked ? ' checked' : '') + '></td>' +
      '<td class="col-notify"><input type="checkbox" class="notify-toggle"' +
        (notifyChecked ? ' checked' : '') + '></td>' +
      '</tr>'
    );
  }

  function render() {
    if (!artists.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">No artists. Run a library scan from the toolbar.</td></tr>';
      footer.textContent = '';
      return;
    }
    body.innerHTML = artists.map(rowHTML).join('');
    footer.textContent = 'Showing ' + artists.length + ' artists';
  }

  async function load() {
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q', search.value.trim());
    if (filterSel.value) params.set('subscription', filterSel.value);
    params.set('sort', sortSel.value);
    body.innerHTML = '<tr><td colspan="5" class="muted">Loading...</td></tr>';
    try {
      const data = await SMT.getJSON('/api/artists?' + params.toString());
      artists = data.artists;
      render();
    } catch (e) {
      body.innerHTML = '<tr><td colspan="5" class="muted">Failed to load artists.</td></tr>';
    }
  }

  async function loadStats() {
    try {
      const s = await SMT.getJSON('/api/stats');
      document.querySelectorAll('[data-stat]').forEach(function (el) {
        const key = el.getAttribute('data-stat');
        if (s[key] != null) el.textContent = s[key];
      });
    } catch (e) {}
  }

  async function setSubscription(id, state) {
    await SMT.postJSON('/api/artists/' + id + '/subscription', { state: state });
    const a = artists.find(function (x) { return x.id === id; });
    if (a) a.subscription = state;
  }

  // --- per row toggles ---
  body.addEventListener('change', function (e) {
    const tr = e.target.closest('tr');
    if (!tr) return;
    const id = parseInt(tr.getAttribute('data-id'), 10);

    if (e.target.classList.contains('sub-toggle')) {
      const notifyBox = tr.querySelector('.notify-toggle');
      let state;
      if (e.target.checked) {
        state = notifyBox.checked ? 'notify' : 'subscribed';
      } else {
        state = 'none';
        notifyBox.checked = false;
      }
      setSubscription(id, state).then(loadStats);
    } else if (e.target.classList.contains('notify-toggle')) {
      const subBox = tr.querySelector('.sub-toggle');
      let state;
      if (e.target.checked) {
        state = 'notify';
        subBox.checked = true;
      } else {
        // Drop back to plain subscribed if it was on, else none.
        state = subBox.checked ? 'subscribed' : 'none';
      }
      setSubscription(id, state).then(loadStats);
    } else if (e.target.classList.contains('row-select')) {
      if (e.target.checked) selected.add(id); else selected.delete(id);
      updateBulkBar();
    }
  });

  // --- selection / bulk ---
  function updateBulkBar() {
    if (selected.size) {
      bulkBar.hidden = false;
      bulkCount.textContent = selected.size + ' selected';
    } else {
      bulkBar.hidden = true;
    }
  }

  selectAll.addEventListener('change', function () {
    body.querySelectorAll('.row-select').forEach(function (cb) {
      cb.checked = selectAll.checked;
      const id = parseInt(cb.closest('tr').getAttribute('data-id'), 10);
      if (selectAll.checked) selected.add(id); else selected.delete(id);
    });
    updateBulkBar();
  });

  bulkBar.addEventListener('click', function (e) {
    const state = e.target.getAttribute('data-bulk');
    if (e.target.id === 'bulk-clear') {
      selected.clear();
      selectAll.checked = false;
      body.querySelectorAll('.row-select').forEach(function (cb) { cb.checked = false; });
      updateBulkBar();
      return;
    }
    if (!state) return;
    const ids = Array.from(selected);
    SMT.postJSON('/api/artists/subscriptions', { ids: ids, state: state }).then(function () {
      selected.clear();
      selectAll.checked = false;
      updateBulkBar();
      load().then(loadStats);
    });
  });

  // --- search / filter ---
  let searchTimer;
  search.addEventListener('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, 200);
  });
  filterSel.addEventListener('change', load);
  sortSel.addEventListener('change', load);

  // --- scan / refresh with progress polling ---
  function pollScan() {
    SMT.getJSON('/api/scan/status').then(function (s) {
      if (s.running) {
        progress.hidden = false;
        progress.textContent = 'Scanning: ' + s.files_seen + ' files, ' +
          s.artists_found + ' artists found. ' + (s.message || '');
        setTimeout(pollScan, 1000);
      } else {
        progress.textContent = 'Scan finished: ' + (s.message || '');
        load().then(loadStats);
        setTimeout(function () { progress.hidden = true; }, 4000);
      }
    });
  }

  function pollRefresh() {
    SMT.getJSON('/api/refresh/status').then(function (s) {
      if (s.running) {
        progress.hidden = false;
        progress.textContent = 'Refreshing following: ' + s.done + '/' + s.total +
          ' (' + (s.message || '') + ')';
        setTimeout(pollRefresh, 1500);
      } else {
        progress.textContent = 'Refresh finished.';
        loadStats();
        setTimeout(function () { progress.hidden = true; }, 4000);
      }
    });
  }

  document.getElementById('btn-scan').addEventListener('click', function () {
    SMT.postJSON('/api/scan', {}).then(function (r) {
      progress.hidden = false;
      progress.textContent = r.error ? r.error : 'Scan started...';
      if (!r.error) setTimeout(pollScan, 800);
    });
  });

  document.getElementById('btn-refresh').addEventListener('click', function () {
    SMT.postJSON('/api/refresh', {}).then(function (r) {
      progress.hidden = false;
      progress.textContent = r.error ? r.error : 'Refresh started...';
      if (!r.error) setTimeout(pollRefresh, 800);
    });
  });

  // Resume progress display if a scan/refresh is already running on load.
  SMT.getJSON('/api/scan/status').then(function (s) { if (s.running) pollScan(); });
  SMT.getJSON('/api/refresh/status').then(function (s) { if (s.running) pollRefresh(); });

  load();
  loadStats();
})();

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
      '<td class="col-ignore"><button type="button" class="ignore-btn" ' +
        'title="Hide this artist">Ignore</button></td>' +
      '</tr>'
    );
  }

  function render() {
    if (!artists.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted">No artists. Run a library scan from the toolbar.</td></tr>';
      footer.textContent = '';
      return;
    }
    body.innerHTML = artists.map(rowHTML).join('');
    footer.textContent = 'Showing ' + artists.length + ' artists';
  }

  async function load(opts) {
    opts = opts || {};
    const params = new URLSearchParams();
    if (search.value.trim()) params.set('q', search.value.trim());
    if (filterSel.value) params.set('subscription', filterSel.value);
    params.set('sort', sortSel.value);
    // 'silent' loads (e.g. live updates during a scan) skip the placeholder and
    // keep the scroll position so they don't disrupt the user.
    if (!opts.silent) body.innerHTML = '<tr><td colspan="6" class="muted">Loading...</td></tr>';
    const scrollY = window.scrollY;
    try {
      const data = await SMT.getJSON('/api/artists?' + params.toString());
      artists = data.artists;
      render();
      if (opts.silent) window.scrollTo(0, scrollY);
    } catch (e) {
      if (!opts.silent) body.innerHTML = '<tr><td colspan="6" class="muted">Failed to load artists.</td></tr>';
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

  // Ignore a single artist: hide it from the list right away.
  body.addEventListener('click', function (e) {
    if (!e.target.classList.contains('ignore-btn')) return;
    const tr = e.target.closest('tr');
    const id = parseInt(tr.getAttribute('data-id'), 10);
    SMT.postJSON('/api/artists/' + id + '/ignore', { ignored: true }).then(function () {
      artists = artists.filter(function (x) { return x.id !== id; });
      selected.delete(id);
      updateBulkBar();
      render();
      loadStats();
    });
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
    if (e.target.id === 'bulk-ignore') {
      const ids = Array.from(selected);
      SMT.postJSON('/api/artists/ignore', { ids: ids, ignored: true }).then(function () {
        selected.clear();
        selectAll.checked = false;
        updateBulkBar();
        load().then(loadStats);
      });
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
  let scanTick = 0;
  function pollScan() {
    SMT.getJSON('/api/scan/status').then(function (s) {
      if (s.running) {
        progress.hidden = false;
        progress.textContent = (s.mode === 'quick' ? 'Quick scan' : 'Full scan') +
          ': ' + s.files_seen + ' files seen, ' + s.artists_found + ' artists. ' +
          (s.message || '');
        // Populate the list live so artists can be subscribed mid-scan.
        scanTick++;
        if (scanTick % 3 === 0) { load({ silent: true }); loadStats(); }
        setTimeout(pollScan, 1000);
      } else {
        progress.textContent = 'Scan finished: ' + (s.message || '');
        scanTick = 0;
        load().then(loadStats);
        setTimeout(function () { progress.hidden = true; }, 4000);
      }
    });
  }

  function pollRefresh() {
    SMT.getJSON('/api/refresh/status').then(function (s) {
      if (s.running) {
        progress.hidden = false;
        progress.textContent = 'Refreshing following: ' + (s.queued || 0) +
          ' queued, ' + (s.processed || 0) + ' done' +
          (s.message ? ' (last: ' + s.message + ')' : '');
        setTimeout(pollRefresh, 2000);
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
      progress.textContent = r.error ? r.error : 'Full scan started...';
      if (!r.error) setTimeout(pollScan, 800);
    });
  });

  document.getElementById('btn-quick-scan').addEventListener('click', function () {
    SMT.postJSON('/api/scan', { quick: true }).then(function (r) {
      progress.hidden = false;
      progress.textContent = r.error ? r.error : 'Quick scan started...';
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

  // --- add / monitor an artist by MusicBrainz link ---
  const mbLink = document.getElementById('mb-link');
  const mbState = document.getElementById('mb-link-state');
  const mbResult = document.getElementById('mb-link-result');
  const mbBtn = document.getElementById('btn-add-link');

  function addByLink() {
    const link = mbLink.value.trim();
    if (!link) return;
    mbBtn.disabled = true;
    mbResult.textContent = 'Looking up...';
    SMT.postJSON('/api/artists/add', { link: link, state: mbState.value })
      .then(function (r) {
        if (r.error) {
          mbResult.textContent = r.error;
        } else {
          mbResult.textContent = (r.created ? 'Added ' : 'Now following ') +
            r.name + ' (' + r.subscription + '). Fetching releases...';
          mbLink.value = '';
          load().then(loadStats);
        }
      })
      .catch(function () { mbResult.textContent = 'Failed to add artist.'; })
      .finally(function () { mbBtn.disabled = false; });
  }

  mbBtn.addEventListener('click', addByLink);
  mbLink.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); addByLink(); }
  });

  // Resume progress display if a scan/refresh is already running on load.
  SMT.getJSON('/api/scan/status').then(function (s) { if (s.running) pollScan(); });
  SMT.getJSON('/api/refresh/status').then(function (s) { if (s.running) pollRefresh(); });

  load();
  loadStats();
})();

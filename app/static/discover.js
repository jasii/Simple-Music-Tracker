// Discover page: new releases from one or more sources, shown as an agenda or
// calendar (via RelView), with per-source show/hide toggles and a Track action.

(function () {
  const viewToggle = document.getElementById('view-toggle');
  const agendaView = document.getElementById('agenda-view');
  const calendarView = document.getElementById('calendar-view');
  const agendaList = document.getElementById('agenda-list');
  const calMount = document.getElementById('calendar');
  const calTitle = document.getElementById('cal-title');
  const sourceFilters = document.getElementById('source-filters');
  const status = document.getElementById('discover-status');

  let allItems = [];
  let sources = [];
  let loaded = false;
  const hidden = new Set();   // source keys the user has unchecked
  try { (JSON.parse(localStorage.getItem('discoverHidden') || '[]')).forEach(function (k) { hidden.add(k); }); } catch (e) {}

  function saveHidden() {
    try { localStorage.setItem('discoverHidden', JSON.stringify(Array.from(hidden))); } catch (e) {}
  }

  function visibleItems() {
    return allItems.filter(function (r) { return !hidden.has(r.source); });
  }

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function agendaRow(r) {
    const img = r.image
      ? '<img src="' + SMT.esc(r.image) + '" alt="" loading="lazy" onerror="this.style.visibility=\'hidden\'">'
      : '';
    const artist = r.artist_url
      ? '<a href="' + SMT.esc(r.artist_url) + '" target="_blank" rel="noopener">' + SMT.esc(r.artist || '') + '</a>'
      : SMT.esc(r.artist || '');
    const album = r.album_url
      ? '<a href="' + SMT.esc(r.album_url) + '" target="_blank" rel="noopener">' + SMT.esc(r.album || '') + '</a>'
      : SMT.esc(r.album || '');
    const action = r.following
      ? '<span class="badge">following</span>'
      : '<button type="button" class="discover-track" data-artist="' + SMT.esc(r.artist || '') + '">Track artist</button>';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + album + ' <span class="src-tag src-' + SMT.esc(r.source) + '">' + SMT.esc(r.source_label || '') + '</span></div>' +
      '<div class="discover-artist">' + artist + '</div>' +
      (r.normalized_date ? '<div class="when">' + fmtDate(r.normalized_date) + '</div>' : '') +
      (r.context ? '<div class="muted discover-context">' + SMT.esc(r.context) + '</div>' : '') +
      '<div class="discover-actions">' + action + '</div>' +
      '</div></div>'
    );
  }

  function calEvent(r) {
    const label = (r.artist ? r.artist + ' – ' : '') + (r.album || '');
    const href = r.album_url || r.artist_url || '#';
    return '<a class="cal-event src-' + SMT.esc(r.source) + '" href="' + SMT.esc(href) + '" ' +
      'target="_blank" rel="noopener" title="' + SMT.esc((r.source_label || '') + ': ' + label) + '">' +
      SMT.esc(label) + '</a>';
  }

  const cal = RelView.calendar({ mount: calMount, titleEl: calTitle, renderEvent: calEvent });
  document.getElementById('cal-prev').addEventListener('click', function () { cal.prev(); });
  document.getElementById('cal-next').addEventListener('click', function () { cal.next(); });
  document.getElementById('cal-today').addEventListener('click', function () { cal.today(); });

  function renderSources() {
    sourceFilters.innerHTML = sources.map(function (s) {
      if (!s.configured) {
        return '<span class="source-chip disabled" title="Not configured">' +
          SMT.esc(s.label) + ' <a href="/settings">set up</a></span>';
      }
      const checked = hidden.has(s.key) ? '' : ' checked';
      const err = s.error ? ' <span class="muted">(error)</span>' : '';
      return '<label class="source-chip"><input type="checkbox" data-source="' + SMT.esc(s.key) + '"' +
        checked + '> ' + SMT.esc(s.label) + err + '</label>';
    }).join('');
  }

  function renderCurrent() {
    if (!calendarView.hidden) { cal.setItems(visibleItems()); cal.render(); }
    else { RelView.agenda(agendaList, visibleItems(), agendaRow, 'No releases to show. Pick a source or add one in Settings.'); }
  }

  function load(refresh) {
    agendaList.innerHTML = '<p class="muted">' + (refresh ? 'Refreshing...' : 'Loading...') + '</p>';
    SMT.getJSON('/api/discover/releases' + (refresh ? '?refresh=1' : '')).then(function (data) {
      sources = data.sources || [];
      allItems = data.items || [];
      loaded = true;
      renderSources();
      const errs = sources.filter(function (s) { return s.error; });
      status.textContent = data.count + ' releases from ' +
        sources.filter(function (s) { return s.configured && !s.error; }).length + ' source(s)' +
        (errs.length ? ' · ' + errs.map(function (s) { return s.label + ': ' + s.error; }).join('; ') : '');
      if (!sources.some(function (s) { return s.configured; })) {
        agendaList.innerHTML = '<p class="muted">No discovery sources configured yet. ' +
          'Add your Last.fm cookie in <a href="/settings">Settings</a>.</p>';
        return;
      }
      renderCurrent();
    }).catch(function () {
      agendaList.innerHTML = '<p class="muted">Failed to load.</p>';
    });
  }

  sourceFilters.addEventListener('change', function (e) {
    const key = e.target.getAttribute('data-source');
    if (!key) return;
    if (e.target.checked) hidden.delete(key); else hidden.add(key);
    saveHidden();
    renderCurrent();
  });

  agendaList.addEventListener('click', function (e) {
    const btn = e.target.closest('.discover-track');
    if (!btn) return;
    const artist = btn.getAttribute('data-artist');
    if (!artist) return;
    btn.disabled = true;
    btn.textContent = 'Tracking...';
    SMT.postJSON('/api/artists/track-by-name', { name: artist, state: 'subscribed' }).then(function (r) {
      if (r.error) { btn.disabled = false; btn.textContent = 'Track artist'; return; }
      allItems.forEach(function (it) {
        if ((it.artist || '').toLowerCase() === artist.toLowerCase()) it.following = true;
      });
      btn.replaceWith(Object.assign(document.createElement('span'),
        { className: 'badge', textContent: 'following' }));
    }).catch(function () { btn.disabled = false; btn.textContent = 'Track artist'; });
  });

  function showView(view) {
    const agenda = view !== 'calendar';
    agendaView.hidden = !agenda;
    calendarView.hidden = agenda;
    viewToggle.querySelectorAll('button').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-view') === view);
    });
    try { localStorage.setItem('discoverView', view); } catch (e) {}
    if (loaded) renderCurrent();
  }

  viewToggle.addEventListener('click', function (e) {
    const view = e.target.getAttribute('data-view');
    if (view) showView(view);
  });
  document.getElementById('discover-refresh').addEventListener('click', function () { load(true); });

  let saved = 'agenda';
  try { saved = localStorage.getItem('discoverView') || 'agenda'; } catch (e) {}
  showView(saved);
  load(false);
})();

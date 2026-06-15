// Upcoming page: agenda (week-by-week) and month calendar, via RelView.

(function () {
  const viewToggle = document.getElementById('view-toggle');
  const typeFilters = document.getElementById('type-filters');
  const agendaView = document.getElementById('agenda-view');
  const calendarView = document.getElementById('calendar-view');
  const agendaList = document.getElementById('agenda-list');
  const calMount = document.getElementById('calendar');
  const calTitle = document.getElementById('cal-title');

  let items = [];
  let loaded = false;

  // Release types the user has toggled off (Album / EP / Single).
  const hiddenTypes = new Set();
  try { (JSON.parse(localStorage.getItem('upcomingHiddenTypes') || '[]')).forEach(function (t) { hiddenTypes.add(t); }); } catch (e) {}
  function saveHiddenTypes() {
    try { localStorage.setItem('upcomingHiddenTypes', JSON.stringify(Array.from(hiddenTypes))); } catch (e) {}
  }
  function visibleItems() {
    // Items without a known type are always shown.
    return items.filter(function (r) { return !hiddenTypes.has(r.primary_type); });
  }

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function agendaRow(r) {
    // Vinyl placeholder shows through when there's no cover or it fails to load.
    const img = '<span class="release-art">' + (r.image_url
      ? '<img src="' + SMT.esc(SMT.art(r.image_url)) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
      : '') + '</span>';
    const albumHref = '/album?artist=' + encodeURIComponent(r.artist_name) +
      '&title=' + encodeURIComponent(r.title) +
      (r.mbid ? '&mbid=' + encodeURIComponent(r.mbid) : '') + '&from=upcoming';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title"><a href="' + albumHref + '">' + SMT.esc(r.title) + '</a></div>' +
      '<div><a href="/artist/' + r.artist_id + '">' + SMT.esc(r.artist_name) + '</a></div>' +
      '<div class="when">' + (r.normalized_date ? fmtDate(r.normalized_date) : 'date TBA') + '</div>' +
      '<div class="muted" style="display: flex; gap: 0.8rem; margin-top: 0.2rem;">' +
      '<a href="https://www.last.fm/music/' + encodeURIComponent(r.artist_name) + '/' + encodeURIComponent(r.title) + '" target="_blank" rel="noopener noreferrer"><img src="/static/last-fm.svg" alt="Last.fm" class="icon-medium"></a>' +
      (r.mbid ? '<a href="https://musicbrainz.org/release-group/' + SMT.esc(r.mbid) + '" target="_blank" rel="noopener noreferrer"><img src="/static/musicbrainz.svg" alt="MusicBrainz" class="icon-medium"></a>' : '') +
      '<a href="https://music.youtube.com/search?q=' + encodeURIComponent(r.artist_name + ' ' + r.title) + '" target="_blank" rel="noopener noreferrer"><img src="/static/youtube-music.svg" alt="YouTube Music" class="icon-medium"></a>' +
      '</div>' +
      (r.primary_type ? '<div class="release-type"><span class="genre-tag">' + SMT.esc(r.primary_type) + '</span></div>' : '') +
      '</div></div>'
    );
  }

  function calEvent(r) {
    return '<a class="cal-event" href="/artist/' + r.artist_id + '" ' +
      'title="' + SMT.esc(r.artist_name + ' – ' + r.title) + '">' +
      SMT.esc(r.artist_name + ' – ' + r.title) + '</a>';
  }

  const cal = RelView.calendar({ mount: calMount, titleEl: calTitle, renderEvent: calEvent });
  document.getElementById('cal-prev').addEventListener('click', function () { cal.prev(); });
  document.getElementById('cal-next').addEventListener('click', function () { cal.next(); });
  document.getElementById('cal-today').addEventListener('click', function () { cal.today(); });

  function renderCurrent() {
    const shown = visibleItems();
    if (!calendarView.hidden) { cal.setItems(shown); cal.render(); }
    else { RelView.agenda(agendaList, shown, agendaRow, 'No upcoming releases from artists you follow.'); }
  }

  // Reflect saved hidden types onto the checkboxes, then wire changes.
  if (typeFilters) {
    typeFilters.querySelectorAll('.type-toggle').forEach(function (cb) {
      cb.checked = !hiddenTypes.has(cb.value);
    });
    typeFilters.addEventListener('change', function (e) {
      const cb = e.target.closest('.type-toggle');
      if (!cb) return;
      if (cb.checked) hiddenTypes.delete(cb.value); else hiddenTypes.add(cb.value);
      saveHiddenTypes();
      if (loaded) renderCurrent();
    });
  }

  function load() {
    agendaList.innerHTML = '<p class="muted">Loading...</p>';
    SMT.getJSON('/api/upcoming/releases').then(function (data) {
      items = data.releases || [];
      loaded = true;
      renderCurrent();
    }).catch(function () {
      agendaList.innerHTML = '<p class="muted">Failed to load.</p>';
    });
  }

  function showView(view) {
    const agenda = view !== 'calendar';
    agendaView.hidden = !agenda;
    calendarView.hidden = agenda;
    viewToggle.querySelectorAll('button').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-view') === view);
    });
    try { localStorage.setItem('upcomingView', view); } catch (e) {}
    if (loaded) renderCurrent();
  }

  viewToggle.addEventListener('click', function (e) {
    const view = e.target.getAttribute('data-view');
    if (view) showView(view);
  });

  let saved = 'agenda';
  try { saved = localStorage.getItem('upcomingView') || 'agenda'; } catch (e) {}
  showView(saved);
  load();
})();

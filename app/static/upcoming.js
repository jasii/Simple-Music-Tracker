// Upcoming page: agenda (week-by-week) and month calendar, via RelView.

(function () {
  const viewToggle = document.getElementById('view-toggle');
  const agendaView = document.getElementById('agenda-view');
  const calendarView = document.getElementById('calendar-view');
  const agendaList = document.getElementById('agenda-list');
  const calMount = document.getElementById('calendar');
  const calTitle = document.getElementById('cal-title');

  let items = [];
  let loaded = false;

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
      '<div><a href="/artist/' + r.artist_id + '">' + SMT.esc(r.artist_name) + '</a> ' +
      (r.primary_type ? '<span class="genre-tag">' + SMT.esc(r.primary_type) + '</span>' : '') + '</div>' +
      '<div class="when">' + (r.normalized_date ? fmtDate(r.normalized_date) : 'date TBA') + '</div>' +
      '<div class="muted" style="display: flex; gap: 0.8rem; margin-top: 0.2rem;">' +
      '<a href="https://www.last.fm/music/' + encodeURIComponent(r.artist_name) + '/' + encodeURIComponent(r.title) + '" target="_blank" rel="noopener noreferrer"><img src="/static/last-fm-light.svg" alt="Last.fm" class="icon-medium"></a>' +
      (r.mbid ? '<a href="https://musicbrainz.org/release-group/' + SMT.esc(r.mbid) + '" target="_blank" rel="noopener noreferrer"><img src="/static/musicbrainz.svg" alt="MusicBrainz" class="icon-medium"></a>' : '') +
      '<a href="https://music.youtube.com/search?q=' + encodeURIComponent(r.artist_name + ' ' + r.title) + '" target="_blank" rel="noopener noreferrer"><img src="/static/youtube-music.svg" alt="YouTube Music" class="icon-medium"></a>' +
      '</div>' +
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
    if (!calendarView.hidden) { cal.setItems(items); cal.render(); }
    else { RelView.agenda(agendaList, items, agendaRow, 'No upcoming releases from artists you follow.'); }
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

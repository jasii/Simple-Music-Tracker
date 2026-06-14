// Upcoming page: an agenda (week-by-week list) and a month calendar view.

(function () {
  const viewToggle = document.getElementById('view-toggle');
  const agendaView = document.getElementById('agenda-view');
  const calendarView = document.getElementById('calendar-view');
  const agendaList = document.getElementById('agenda-list');
  const cal = document.getElementById('calendar');
  const calTitle = document.getElementById('cal-title');

  // --- date helpers (all local time, midnight) ---
  function iso(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }
  function parseISO(s) { return new Date(s + 'T00:00:00'); }
  function weekStart(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    x.setDate(x.getDate() - x.getDay()); // back to Sunday
    return x;
  }
  function fmtRange(start, end) {
    const opts = { month: 'short', day: 'numeric' };
    return start.toLocaleDateString(undefined, opts) + ' – ' +
           end.toLocaleDateString(undefined, opts);
  }

  function releaseRow(r) {
    const d = parseISO(r.normalized_date);
    const day = d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    const img = r.image_url
      ? '<img src="' + SMT.esc(r.image_url) + '" alt="" loading="lazy" ' +
        'onerror="this.style.visibility=\'hidden\'">'
      : '';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + SMT.esc(r.title) + '</div>' +
      '<div><a href="/artist/' + r.artist_id + '">' + SMT.esc(r.artist_name) + '</a> ' +
      '<span class="muted">' + SMT.esc(r.primary_type || '') + '</span></div>' +
      '<div class="when">' + day + '</div>' +
      '</div></div>'
    );
  }

  // --- agenda (week by week) ---
  function renderAgenda(releases) {
    if (!releases.length) {
      agendaList.innerHTML = '<p class="muted">No upcoming releases from artists you follow.</p>';
      return;
    }
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const thisWeek = weekStart(today).getTime();

    const buckets = new Map();
    releases.forEach(function (r) {
      const ws = weekStart(parseISO(r.normalized_date)).getTime();
      if (!buckets.has(ws)) buckets.set(ws, []);
      buckets.get(ws).push(r);
    });

    const weeks = Array.from(buckets.keys()).sort(function (a, b) { return a - b; });
    let html = '';
    weeks.forEach(function (ws) {
      const start = new Date(ws);
      const end = new Date(ws); end.setDate(end.getDate() + 6);
      const diff = Math.round((ws - thisWeek) / (7 * 86400000));
      let rel = '';
      if (diff === 0) rel = 'This week';
      else if (diff === 1) rel = 'Next week';
      else if (diff > 1) rel = 'In ' + diff + ' weeks';
      html += '<section class="agenda-week"><h3 class="agenda-head">' +
        (rel ? '<span class="agenda-rel">' + rel + '</span> ' : '') +
        '<span class="muted">' + fmtRange(start, end) + '</span></h3>';
      buckets.get(ws).forEach(function (r) { html += releaseRow(r); });
      html += '</section>';
    });
    agendaList.innerHTML = html;
  }

  let agendaLoaded = false;
  function loadAgenda() {
    agendaList.innerHTML = '<p class="muted">Loading...</p>';
    SMT.getJSON('/api/upcoming/releases').then(function (data) {
      renderAgenda(data.releases);
      agendaLoaded = true;
    }).catch(function () {
      agendaList.innerHTML = '<p class="muted">Failed to load.</p>';
    });
  }

  // --- calendar (month grid) ---
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  let calYear, calMonth; // month is 0-based

  function renderCalendar() {
    const first = new Date(calYear, calMonth, 1);
    calTitle.textContent = first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
    const gridStart = weekStart(first);
    const gridEnd = new Date(gridStart); gridEnd.setDate(gridEnd.getDate() + 41); // 6 weeks

    cal.innerHTML = '<p class="muted">Loading...</p>';
    SMT.getJSON('/api/upcoming/releases?from=' + iso(gridStart) + '&to=' + iso(gridEnd))
      .then(function (data) {
        const byDay = {};
        data.releases.forEach(function (r) {
          (byDay[r.normalized_date] = byDay[r.normalized_date] || []).push(r);
        });
        const todayIso = iso(new Date());
        let html = '<div class="cal-grid">';
        DOW.forEach(function (d) { html += '<div class="cal-dow">' + d + '</div>'; });
        for (let i = 0; i < 42; i++) {
          const d = new Date(gridStart); d.setDate(d.getDate() + i);
          const dIso = iso(d);
          const inMonth = d.getMonth() === calMonth;
          const items = byDay[dIso] || [];
          html += '<div class="cal-cell' + (inMonth ? '' : ' cal-out') +
            (dIso === todayIso ? ' cal-today' : '') + '">' +
            '<div class="cal-day">' + d.getDate() + '</div>';
          items.forEach(function (r) {
            html += '<a class="cal-event" href="/artist/' + r.artist_id + '" ' +
              'title="' + SMT.esc(r.artist_name + ' – ' + r.title +
                ' (' + (r.primary_type || '') + ')') + '">' +
              SMT.esc(r.artist_name + ' – ' + r.title) + '</a>';
          });
          html += '</div>';
        }
        html += '</div>';
        cal.innerHTML = html;
      }).catch(function () {
        cal.innerHTML = '<p class="muted">Failed to load.</p>';
      });
  }

  let calInit = false;
  function initCalendar() {
    const now = new Date();
    calYear = now.getFullYear();
    calMonth = now.getMonth();
    calInit = true;
    renderCalendar();
  }

  document.getElementById('cal-prev').addEventListener('click', function () {
    calMonth--; if (calMonth < 0) { calMonth = 11; calYear--; }
    renderCalendar();
  });
  document.getElementById('cal-next').addEventListener('click', function () {
    calMonth++; if (calMonth > 11) { calMonth = 0; calYear++; }
    renderCalendar();
  });
  document.getElementById('cal-today').addEventListener('click', function () {
    const now = new Date(); calYear = now.getFullYear(); calMonth = now.getMonth();
    renderCalendar();
  });

  // --- view switching (persisted) ---
  function showView(view) {
    const agenda = view !== 'calendar';
    agendaView.hidden = !agenda;
    calendarView.hidden = agenda;
    viewToggle.querySelectorAll('button').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-view') === view);
    });
    try { localStorage.setItem('upcomingView', view); } catch (e) {}
    if (agenda && !agendaLoaded) loadAgenda();
    if (!agenda && !calInit) initCalendar();
  }

  viewToggle.addEventListener('click', function (e) {
    const view = e.target.getAttribute('data-view');
    if (view) showView(view);
  });

  let saved = 'agenda';
  try { saved = localStorage.getItem('upcomingView') || 'agenda'; } catch (e) {}
  showView(saved);
})();

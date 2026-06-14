// Shared agenda (week-by-week) and calendar (month grid) rendering, used by
// the Upcoming and Discover pages. Works off an in-memory list of items that
// each have a `normalized_date` (YYYY-MM-DD); callers supply how to render an
// agenda row and a calendar event.

(function () {
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

  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  function agenda(container, items, renderItem, emptyMsg) {
    const dated = items.filter(function (r) { return r.normalized_date; });
    const undated = items.filter(function (r) { return !r.normalized_date; });
    if (!dated.length && !undated.length) {
      container.innerHTML = '<p class="muted">' + (emptyMsg || 'Nothing to show.') + '</p>';
      return;
    }
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const thisWeek = weekStart(today).getTime();

    const buckets = new Map();
    dated.forEach(function (r) {
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
      else if (diff === -1) rel = 'Last week';
      html += '<section class="agenda-week"><h3 class="agenda-head">' +
        (rel ? '<span class="agenda-rel">' + rel + '</span> ' : '') +
        '<span class="muted">' + fmtRange(start, end) + '</span></h3>';
      buckets.get(ws).forEach(function (r) { html += renderItem(r); });
      html += '</section>';
    });
    if (undated.length) {
      html += '<section class="agenda-week"><h3 class="agenda-head"><span class="muted">Date TBA</span></h3>';
      undated.forEach(function (r) { html += renderItem(r); });
      html += '</section>';
    }
    container.innerHTML = html;
  }

  function calendar(opts) {
    // opts: { mount, titleEl, renderEvent }
    let items = [];
    const now = new Date();
    let year = now.getFullYear();
    let month = now.getMonth(); // 0-based

    function render() {
      const first = new Date(year, month, 1);
      opts.titleEl.textContent = first.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
      const gridStart = weekStart(first);

      const byDay = {};
      items.forEach(function (r) {
        if (r.normalized_date) (byDay[r.normalized_date] = byDay[r.normalized_date] || []).push(r);
      });
      const todayIso = iso(new Date());
      let html = '<div class="cal-grid">';
      DOW.forEach(function (d) { html += '<div class="cal-dow">' + d + '</div>'; });
      for (let i = 0; i < 42; i++) {
        const d = new Date(gridStart); d.setDate(d.getDate() + i);
        const dIso = iso(d);
        const inMonth = d.getMonth() === month;
        const evs = byDay[dIso] || [];
        html += '<div class="cal-cell' + (inMonth ? '' : ' cal-out') +
          (dIso === todayIso ? ' cal-today' : '') + '">' +
          '<div class="cal-day">' + d.getDate() + '</div>';
        evs.forEach(function (r) { html += opts.renderEvent(r); });
        html += '</div>';
      }
      html += '</div>';
      opts.mount.innerHTML = html;
    }

    return {
      setItems: function (x) { items = x; },
      render: render,
      prev: function () { month--; if (month < 0) { month = 11; year--; } render(); },
      next: function () { month++; if (month > 11) { month = 0; year++; } render(); },
      today: function () { const n = new Date(); year = n.getFullYear(); month = n.getMonth(); render(); },
    };
  }

  window.RelView = { agenda: agenda, calendar: calendar };
})();

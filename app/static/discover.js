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

  // A merged item carries several sources; show it if any of them is visible.
  function itemSources(r) {
    return (r.sources && r.sources.length) ? r.sources : [{ key: r.source, label: r.source_label }];
  }
  function visibleItems() {
    return allItems.filter(function (r) {
      return itemSources(r).some(function (s) { return !hidden.has(s.key); });
    });
  }

  function fmtDate(iso) {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function agendaRow(r) {
    // The art box always shows a vinyl placeholder as its background; the cover
    // sits on top. No image, or a broken one (onerror hides it), reveals the vinyl.
    const img = '<span class="release-art">' + (r.image
      ? '<img src="' + SMT.esc(SMT.art(r.image)) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
      : '') + '</span>';
    const artist = r.artist_url
      ? '<a href="' + SMT.esc(r.artist_url) + '" target="_blank" rel="noopener">' + SMT.esc(r.artist || '') + '</a>'
      : SMT.esc(r.artist || '');
    // Link the title to our in-app album page (tracks + previews). Fall back to
    // the source's external link, then plain text, when we can't build one.
    let album;
    if (r.artist && r.album) {
      const albumHref = '/album?artist=' + encodeURIComponent(r.artist) +
        '&title=' + encodeURIComponent(r.album) +
        (r.mbid ? '&mbid=' + encodeURIComponent(r.mbid) : '') + '&from=discover';
      album = '<a href="' + albumHref + '">' + SMT.esc(r.album) + '</a>';
    } else if (r.album_url) {
      album = '<a href="' + SMT.esc(r.album_url) + '" target="_blank" rel="noopener">' + SMT.esc(r.album || '') + '</a>';
    } else {
      album = SMT.esc(r.album || '');
    }
    const genres = (r.genres && r.genres.length)
      ? '<div class="discover-genres">' + r.genres.map(function (g) {
          return '<span class="genre-tag">' + SMT.esc(g) + '</span>';
        }).join('') + '</div>'
      : '';
    const action = r.following
      ? '<span class="badge following-badge">following<button type="button" class="discover-unfollow" title="Unfollow" data-artist="' + SMT.esc(r.artist || '') + '">&times;</button></span>'
      : '<button type="button" class="discover-track badge" data-artist="' + SMT.esc(r.artist || '') + '">Follow</button>';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + album + ' ' + itemSources(r).map(function (s) {
        return '<span class="src-tag src-' + SMT.esc(s.key) + '">' + SMT.esc(s.label || '') + '</span>';
      }).join(' ') + '</div>' +
      '<div class="discover-artist">' + artist + '</div>' +
      (r.normalized_date ? '<div class="when">' + fmtDate(r.normalized_date) + '</div>' : '') +
      (r.context ? '<div class="muted discover-context">' + SMT.esc(r.context) + '</div>' : '') +
      genres +
      ((r.artist && r.album) ? SMT.releaseIcons(r.artist, r.album, r.mbid) : '') +
      '<div class="discover-actions">' + action + '</div>' +
      '</div></div>'
    );
  }

  const SRC_COLORS = { lastfm: '#d51007', metacritic: '#ffcc33' };

  function calEvent(r) {
    const label = (r.artist ? r.artist + ' – ' : '') + (r.album || '');
    const href = (r.artist && r.album)
      ? '/album?artist=' + encodeURIComponent(r.artist) + '&title=' + encodeURIComponent(r.album) +
        (r.mbid ? '&mbid=' + encodeURIComponent(r.mbid) : '') + '&from=discover'
      : (r.album_url || r.artist_url || '#');
    // Only sources still toggled on get a bar, so hiding a source drops its bar.
    const srcs = itemSources(r).filter(function (s) { return !hidden.has(s.key); });
    const classes = 'cal-event ' + srcs.map(function (s) { return 'src-' + SMT.esc(s.key); }).join(' ');
    // Multiple sources: stack one left bar per source (e.g. red + yellow) using
    // inset shadows; a single source keeps the plain left border from CSS.
    let style = '';
    const colors = srcs.map(function (s) { return SRC_COLORS[s.key]; }).filter(Boolean);
    if (colors.length > 1) {
      const bars = colors.map(function (c, i) { return 'inset ' + (2 * (i + 1)) + 'px 0 0 0 ' + c; });
      style = ' style="border-left:none;box-shadow:' + bars.join(',') + ';padding-left:' + (2 * colors.length + 4) + 'px"';
    }
    const tip = srcs.map(function (s) { return s.label; }).filter(Boolean).join(' + ') + ': ' + label;
    return '<a class="' + classes + '" href="' + SMT.esc(href) + '"' + style + ' ' +
      'target="_blank" rel="noopener" title="' + SMT.esc(tip) + '">' +
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
      return '<span class="source-chip"><label><input type="checkbox" data-source="' + SMT.esc(s.key) + '"' +
        checked + '> ' + SMT.esc(s.label) + err + '</label>' +
        '<button type="button" class="source-refresh" data-refresh="' + SMT.esc(s.key) + '"' +
        ' title="Refresh ' + SMT.esc(s.label) + '">&#x21bb;</button></span>';
    }).join('');
  }

  function renderCurrent() {
    if (!calendarView.hidden) { cal.setItems(visibleItems()); cal.render(); }
    else { RelView.agenda(agendaList, visibleItems(), agendaRow, 'No releases to show. Pick a source or add one in Settings.'); }
  }

  let pollTimer = null;

  // Re-poll (without disturbing what's on screen) while any source is still
  // scraping in the background, so freshly-scraped releases appear when ready.
  function schedulePoll(refreshing) {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (refreshing) pollTimer = setTimeout(function () { load(null, true); }, 5000);
  }

  // refresh: falsy = use cache, a source key = re-scrape that source, 'all' = every source.
  // poll: true when this is a background re-check (don't blank the list first).
  function load(refresh, poll) {
    if (!poll) {
      agendaList.innerHTML = '<p class="muted">' + (loaded ? 'Refreshing...' : 'Loading...') + '</p>';
    }
    const q = refresh ? ('?refresh=' + encodeURIComponent(refresh)) : '';
    SMT.getJSON('/api/discover/releases' + q).then(function (data) {
      sources = data.sources || [];
      allItems = data.items || [];
      loaded = true;
      renderSources();
      const errs = sources.filter(function (s) { return s.error; });
      const busy = sources.filter(function (s) { return s.refreshing; });
      status.textContent = data.count + ' releases from ' +
        sources.filter(function (s) { return s.configured && !s.error; }).length + ' source(s)' +
        (busy.length ? ' · refreshing ' + busy.map(function (s) { return s.label; }).join(', ') + '...' : '') +
        (errs.length ? ' · ' + errs.map(function (s) { return s.label + ': ' + s.error; }).join('; ') : '');
      if (!sources.some(function (s) { return s.configured; })) {
        agendaList.innerHTML = '<p class="muted">No discovery sources configured yet. ' +
          'Add your Last.fm cookie in <a href="/settings">Settings</a>.</p>';
        return;
      }
      renderCurrent();
      schedulePoll(data.refreshing);
    }).catch(function () {
      if (!poll) agendaList.innerHTML = '<p class="muted">Failed to load.</p>';
    });
  }

  sourceFilters.addEventListener('change', function (e) {
    const key = e.target.getAttribute('data-source');
    if (!key) return;
    if (e.target.checked) hidden.delete(key); else hidden.add(key);
    saveHidden();
    renderCurrent();
  });

  sourceFilters.addEventListener('click', function (e) {
    const btn = e.target.closest('.source-refresh');
    if (!btn) return;
    load(btn.getAttribute('data-refresh'));
  });

  function followingBadge(artist) {
    return '<span class="badge following-badge">following<button type="button" ' +
      'class="discover-unfollow" title="Unfollow" data-artist="' + SMT.esc(artist) + '">&times;</button></span>';
  }
  function followButton(artist) {
    return '<button type="button" class="discover-track badge" data-artist="' + SMT.esc(artist) + '">Follow</button>';
  }
  function setFollowing(artist, following) {
    allItems.forEach(function (it) {
      if ((it.artist || '').toLowerCase() === artist.toLowerCase()) it.following = following;
    });
  }

  agendaList.addEventListener('click', function (e) {
    const followBtn = e.target.closest('.discover-track');
    const unfollowBtn = e.target.closest('.discover-unfollow');

    if (followBtn) {
      const artist = followBtn.getAttribute('data-artist');
      if (!artist) return;
      followBtn.disabled = true;
      followBtn.textContent = 'Following...';
      SMT.postJSON('/api/artists/track-by-name', { name: artist, state: 'subscribed' }).then(function (r) {
        if (r.error) { followBtn.disabled = false; followBtn.textContent = 'Follow'; return; }
        setFollowing(artist, true);
        followBtn.outerHTML = followingBadge(artist);
      }).catch(function () { followBtn.disabled = false; followBtn.textContent = 'Follow'; });
      return;
    }

    if (unfollowBtn) {
      const artist = unfollowBtn.getAttribute('data-artist');
      if (!artist) return;
      const badge = unfollowBtn.closest('.following-badge');
      unfollowBtn.disabled = true;
      SMT.postJSON('/api/artists/track-by-name', { name: artist, state: 'none' }).then(function (r) {
        if (r.error) { unfollowBtn.disabled = false; return; }
        setFollowing(artist, false);
        if (badge) badge.outerHTML = followButton(artist);
      }).catch(function () { unfollowBtn.disabled = false; });
    }
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
  document.getElementById('discover-refresh').addEventListener('click', function () { load('all'); });

  let saved = 'agenda';
  try { saved = localStorage.getItem('discoverView') || 'agenda'; } catch (e) {}
  showView(saved);
  load(false);
})();

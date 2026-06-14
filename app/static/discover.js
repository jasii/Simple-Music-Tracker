// Discover page: scraped Last.fm coming-soon releases, with a track action.

(function () {
  const grid = document.getElementById('lastfm-releases');
  const status = document.getElementById('lastfm-status');
  const refreshBtn = document.getElementById('lastfm-refresh');

  function card(r) {
    const img = r.image
      ? '<img src="' + SMT.esc(r.image) + '" alt="" loading="lazy" ' +
        'onerror="this.style.visibility=\'hidden\'">'
      : '<div class="discover-noart"></div>';
    const artistLink = r.artist_url
      ? '<a href="' + SMT.esc(r.artist_url) + '" target="_blank" rel="noopener">' + SMT.esc(r.artist || '') + '</a>'
      : SMT.esc(r.artist || '');
    const albumLink = r.album_url
      ? '<a href="' + SMT.esc(r.album_url) + '" target="_blank" rel="noopener" class="discover-album">' + SMT.esc(r.album || '') + '</a>'
      : '<span class="discover-album">' + SMT.esc(r.album || '') + '</span>';

    let action;
    if (r.following) {
      action = '<span class="badge">following</span>';
    } else {
      action = '<button type="button" class="discover-track" data-artist="' +
        SMT.esc(r.artist || '') + '">Track artist</button>';
    }

    return (
      '<div class="discover-card">' + img +
      '<div class="discover-body">' +
      '<div>' + albumLink + '</div>' +
      '<div class="discover-artist">' + artistLink + '</div>' +
      (r.release_date ? '<div class="when">' + SMT.esc(r.release_date) + '</div>' : '') +
      (r.context ? '<div class="muted discover-context">' + SMT.esc(r.context) + '</div>' : '') +
      '<div class="discover-actions">' + action + '</div>' +
      '</div></div>'
    );
  }

  function load(refresh) {
    grid.innerHTML = '<p class="muted">' + (refresh ? 'Refreshing from Last.fm...' : 'Loading...') + '</p>';
    status.textContent = '';
    SMT.getJSON('/api/discover/lastfm' + (refresh ? '?refresh=1' : '')).then(function (data) {
      if (!data.configured) {
        grid.innerHTML = '<p class="muted">' + SMT.esc(data.error || '') +
          ' <a href="/settings">Open Settings</a></p>';
        return;
      }
      if (data.error) {
        grid.innerHTML = '<p class="muted">Could not load: ' + SMT.esc(data.error) + '</p>';
        return;
      }
      if (!data.items.length) {
        grid.innerHTML = '<p class="muted">No releases found. Your cookie may have expired &mdash; ' +
          're-copy it in <a href="/settings">Settings</a>.</p>';
        return;
      }
      status.textContent = data.count + ' releases' + (data.cached ? ' (cached)' : '');
      grid.innerHTML = data.items.map(card).join('');
    }).catch(function () {
      grid.innerHTML = '<p class="muted">Failed to load.</p>';
    });
  }

  grid.addEventListener('click', function (e) {
    const btn = e.target.closest('.discover-track');
    if (!btn) return;
    const artist = btn.getAttribute('data-artist');
    if (!artist) return;
    btn.disabled = true;
    btn.textContent = 'Tracking...';
    SMT.postJSON('/api/artists/track-by-name', { name: artist, state: 'subscribed' })
      .then(function (r) {
        if (r.error) { btn.disabled = false; btn.textContent = 'Track artist'; return; }
        btn.replaceWith(Object.assign(document.createElement('span'),
          { className: 'badge', textContent: 'following' }));
      })
      .catch(function () { btn.disabled = false; btn.textContent = 'Track artist'; });
  });

  refreshBtn.addEventListener('click', function () { load(true); });

  load(false);
})();

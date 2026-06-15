// Album detail page: tracklist + Last.fm/iTunes audio previews.

(function () {
  const header = document.getElementById('album-header');
  const artistEl = document.getElementById('album-artist');
  const linksEl = document.getElementById('album-links');
  const sourceEl = document.getElementById('album-source');
  const tracksEl = document.getElementById('album-tracks');

  function fmtDuration(sec) {
    if (!sec) return '';
    const m = Math.floor(sec / 60);
    const s = String(sec % 60).padStart(2, '0');
    return m + ':' + s;
  }

  function trackRow(t, i) {
    const name = t.url
      ? '<a href="' + SMT.esc(t.url) + '" target="_blank" rel="noopener">' + SMT.esc(t.name) + '</a>'
      : SMT.esc(t.name);
    const dur = t.duration ? '<span class="muted track-dur">' + fmtDuration(t.duration) + '</span>' : '';
    const preview = t.preview_url
      ? '<audio class="track-preview" controls preload="none" src="' + SMT.esc(t.preview_url) + '"></audio>'
      : '';
    return (
      '<div class="track-row">' +
      '<span class="muted track-num">' + (i + 1) + '</span>' +
      '<span class="track-name">' + name + '</span>' +
      preview + dur +
      '</div>'
    );
  }

  // Follow / unfollow toggle (mirrors the Discover badge).
  let following = false;
  function followControl() {
    return following
      ? '<span class="badge following-badge">following<button type="button" class="album-unfollow" title="Unfollow">&times;</button></span>'
      : '<button type="button" class="discover-track badge album-follow">Follow</button>';
  }
  function renderArtist(data) {
    const name = SMT.esc(data.artist || '');
    const link = data.artist_id
      ? '<a href="/artist/' + data.artist_id + '">' + name + '</a>'
      : name;
    artistEl.innerHTML = link + ' ' + followControl();
  }
  artistEl.addEventListener('click', function (e) {
    const followBtn = e.target.closest('.album-follow');
    const unfollowBtn = e.target.closest('.album-unfollow');
    if (!followBtn && !unfollowBtn) return;
    const state = followBtn ? 'subscribed' : 'none';
    const btn = followBtn || unfollowBtn;
    btn.disabled = true;
    SMT.postJSON('/api/artists/track-by-name', { name: ALBUM.artist, state: state }).then(function (r) {
      if (r.error) { btn.disabled = false; return; }
      following = (state !== 'none');
      // Re-render so a freshly-followed artist also gains its library link.
      artistEl.querySelector('.badge, .album-follow').outerHTML = followControl();
    }).catch(function () { btn.disabled = false; });
  });

  function render(data) {
    following = !!data.following;
    renderArtist(data);
    // Cover art box: vinyl placeholder shows when there's no image or it fails.
    const art = document.createElement('span');
    art.className = 'release-art album-art';
    if (data.image) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.src = SMT.art(data.image);
      img.onerror = function () { img.style.display = 'none'; };
      art.appendChild(img);
    }
    header.insertBefore(art, header.firstChild);
    linksEl.innerHTML = SMT.releaseIcons(ALBUM.artist, ALBUM.title, ALBUM.mbid);

    const tracks = data.tracks || [];
    if (!tracks.length) {
      tracksEl.innerHTML = '<p class="muted">Tracklist not available yet.</p>';
      return;
    }
    tracksEl.innerHTML = tracks.map(trackRow).join('');
  }

  const q = '?artist=' + encodeURIComponent(ALBUM.artist) +
    '&title=' + encodeURIComponent(ALBUM.title) +
    (ALBUM.mbid ? '&mbid=' + encodeURIComponent(ALBUM.mbid) : '');
  SMT.getJSON('/api/album' + q).then(render).catch(function () {
    tracksEl.innerHTML = '<p class="muted">Failed to load tracklist.</p>';
  });
})();

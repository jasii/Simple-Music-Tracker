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
      dur + preview +
      '</div>'
    );
  }

  function render(data) {
    artistEl.textContent = data.artist || '';
    // Cover art box: vinyl placeholder shows when there's no image or it fails.
    const art = document.createElement('span');
    art.className = 'release-art album-art';
    if (data.image) {
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.src = data.image;
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

// Artist detail page: subscription radios, refresh, and release list.

(function () {
  const id = window.ARTIST_ID;
  const releasesEl = document.getElementById('artist-releases');

  function releaseHTML(r) {
    const img = r.image_url
      ? '<img src="' + SMT.esc(r.image_url) + '" alt="" loading="lazy" ' +
        'onerror="this.style.visibility=\'hidden\'">'
      : '';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + SMT.esc(r.title) + '</div>' +
      '<div class="muted">' + SMT.esc(r.primary_type || '') + '</div>' +
      '<div class="when">' + (r.release_date ? SMT.formatDate(r.release_date) : 'date TBA') + '</div>' +
      '</div></div>'
    );
  }

  async function load() {
    try {
      const data = await SMT.getJSON('/api/artists/' + id);
      if (!data.releases || !data.releases.length) {
        releasesEl.innerHTML = '<p class="muted">No tracked releases yet. Use "Refresh now" to fetch.</p>';
        return;
      }
      releasesEl.innerHTML = data.releases.map(releaseHTML).join('');
    } catch (e) {
      releasesEl.innerHTML = '<p class="muted">Failed to load releases.</p>';
    }
  }

  document.querySelectorAll('input[name="sub"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (radio.checked) {
        SMT.postJSON('/api/artists/' + id + '/subscription', { state: radio.value });
      }
    });
  });

  const refreshBtn = document.getElementById('btn-refresh-artist');
  refreshBtn.addEventListener('click', function () {
    refreshBtn.textContent = 'Refreshing...';
    SMT.postJSON('/api/artists/' + id + '/refresh', {}).then(function () {
      // Give the background fetch a moment, then reload.
      setTimeout(function () {
        refreshBtn.textContent = 'Refresh now';
        load();
      }, 4000);
    });
  });

  load();
})();

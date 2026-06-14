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

  // Monitor-type checkboxes: persist the selection and re-fetch releases.
  const mtypeResult = document.getElementById('mtype-result');
  document.querySelectorAll('.mtype').forEach(function (cb) {
    cb.addEventListener('change', function () {
      const types = Array.from(document.querySelectorAll('.mtype:checked'))
        .map(function (x) { return x.value; });
      mtypeResult.textContent = 'Saving...';
      SMT.postJSON('/api/artists/' + id + '/monitor-types', { types: types })
        .then(function (r) {
          // Server enforces a non-empty selection; reflect what it kept.
          const kept = r.monitor_types || types;
          document.querySelectorAll('.mtype').forEach(function (x) {
            x.checked = kept.indexOf(x.value) !== -1;
          });
          mtypeResult.textContent = 'Saved (' + kept.join(', ') + ')';
          setTimeout(function () { mtypeResult.textContent = ''; }, 2500);
          setTimeout(load, 3000);
        })
        .catch(function () { mtypeResult.textContent = 'Failed.'; });
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

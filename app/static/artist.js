// Artist detail page: subscription, monitor types, match-to-MusicBrainz,
// merge another artist in, and the on-demand discography (albums/EPs/singles).

(function () {
  const id = window.ARTIST_ID;
  const disco = document.getElementById('discography');
  const discoSource = document.getElementById('disco-source');

  const CATS = [['album', 'Albums'], ['ep', 'EPs'], ['single', 'Singles']];

  function releaseHTML(r) {
    const img = r.image_url
      ? '<img src="' + SMT.esc(r.image_url) + '" alt="" loading="lazy" ' +
        'onerror="this.style.visibility=\'hidden\'">'
      : '';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + SMT.esc(r.title) + '</div>' +
      '<div class="when">' + (r.release_date ? SMT.formatDate(r.release_date) : 'date TBA') + '</div>' +
      '</div></div>'
    );
  }

  function renderDisco(groups) {
    const auto = (window.AUTOHIDE || '').split(',').filter(Boolean);
    let html = '';
    CATS.forEach(function (cat) {
      const key = cat[0], label = cat[1];
      const items = groups[key] || [];
      const hidden = auto.indexOf(key) !== -1;
      html +=
        '<section class="disco-cat" data-cat="' + key + '">' +
        '<h3 class="disco-head">' + label +
        ' <span class="muted">(' + items.length + ')</span> ' +
        '<button type="button" class="toggle-cat">' + (hidden ? 'Show' : 'Hide') + '</button></h3>' +
        '<div class="release-list disco-items"' + (hidden ? ' hidden' : '') + '>' +
        (items.length ? items.map(releaseHTML).join('') : '<p class="muted">None.</p>') +
        '</div></section>';
    });
    disco.innerHTML = html;
  }

  // Toggle show/hide for a category.
  disco.addEventListener('click', function (e) {
    if (!e.target.classList.contains('toggle-cat')) return;
    const section = e.target.closest('.disco-cat');
    const items = section.querySelector('.disco-items');
    const hidden = !items.hidden;
    items.hidden = hidden;
    e.target.textContent = hidden ? 'Show' : 'Hide';
  });

  function loadDiscography() {
    disco.innerHTML = '<p class="muted">Loading from MusicBrainz...</p>';
    SMT.getJSON('/api/artists/' + id + '/discography').then(function (data) {
      if (!data.mbid) {
        disco.innerHTML = '<p class="muted">No MusicBrainz match for this artist yet. ' +
          'Use Tools above to match a MusicBrainz URL.</p>';
        return;
      }
      discoSource.textContent = data.error ? '(MusicBrainz error)' : '(from MusicBrainz)';
      renderDisco(data.groups || { album: [], ep: [], single: [] });
    }).catch(function () {
      disco.innerHTML = '<p class="muted">Failed to load discography.</p>';
    });
  }

  // --- subscription radios ---
  document.querySelectorAll('input[name="sub"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      if (radio.checked) {
        SMT.postJSON('/api/artists/' + id + '/subscription', { state: radio.value });
      }
    });
  });

  // --- monitor-type checkboxes ---
  const mtypeResult = document.getElementById('mtype-result');
  document.querySelectorAll('.mtype').forEach(function (cb) {
    cb.addEventListener('change', function () {
      const types = Array.from(document.querySelectorAll('.mtype:checked'))
        .map(function (x) { return x.value; });
      mtypeResult.textContent = 'Saving...';
      SMT.postJSON('/api/artists/' + id + '/monitor-types', { types: types })
        .then(function (r) {
          const kept = r.monitor_types || types;
          document.querySelectorAll('.mtype').forEach(function (x) {
            x.checked = kept.indexOf(x.value) !== -1;
          });
          mtypeResult.textContent = 'Saved (' + kept.join(', ') + ')';
          setTimeout(function () { mtypeResult.textContent = ''; }, 2500);
        })
        .catch(function () { mtypeResult.textContent = 'Failed.'; });
    });
  });

  // --- refresh tracked releases ---
  const refreshBtn = document.getElementById('btn-refresh-artist');
  refreshBtn.addEventListener('click', function () {
    refreshBtn.textContent = 'Refreshing...';
    SMT.postJSON('/api/artists/' + id + '/refresh', {}).then(function () {
      setTimeout(function () { refreshBtn.textContent = 'Refresh now'; }, 3000);
    });
  });

  // --- match to a MusicBrainz URL ---
  const matchResult = document.getElementById('match-result');
  document.getElementById('btn-match').addEventListener('click', function () {
    const link = document.getElementById('mb-match').value.trim();
    if (!link) return;
    matchResult.textContent = 'Matching...';
    SMT.postJSON('/api/artists/' + id + '/mbid', { link: link }).then(function (r) {
      if (r.error) { matchResult.textContent = r.error; return; }
      matchResult.textContent = 'Matched: ' + r.matched_name;
      loadDiscography();
    }).catch(function () { matchResult.textContent = 'Failed.'; });
  });

  // --- merge another artist into this one ---
  const mergeSearch = document.getElementById('merge-search');
  const mergeMatches = document.getElementById('merge-matches');
  const mergeResult = document.getElementById('merge-result');
  let mergeTimer;

  function renderMergeMatches(items) {
    const others = items.filter(function (a) { return a.id !== id; });
    if (!others.length) { mergeMatches.innerHTML = ''; return; }
    mergeMatches.innerHTML = others.slice(0, 20).map(function (a) {
      return '<button type="button" class="merge-pick" data-id="' + a.id + '" ' +
        'data-name="' + SMT.esc(a.name) + '">' + SMT.esc(a.name) +
        ' <span class="muted">(' + (a.track_count || 0) + ' tracks)</span></button>';
    }).join('');
  }

  mergeSearch.addEventListener('input', function () {
    clearTimeout(mergeTimer);
    const q = mergeSearch.value.trim();
    if (q.length < 2) { mergeMatches.innerHTML = ''; return; }
    mergeTimer = setTimeout(function () {
      SMT.getJSON('/api/artists?ignored=all&q=' + encodeURIComponent(q))
        .then(function (data) { renderMergeMatches(data.artists); });
    }, 250);
  });

  mergeMatches.addEventListener('click', function (e) {
    const btn = e.target.closest('.merge-pick');
    if (!btn) return;
    const sourceId = parseInt(btn.getAttribute('data-id'), 10);
    const name = btn.getAttribute('data-name');
    if (!window.confirm('Merge "' + name + '" into this artist? This removes "' + name + '".')) return;
    mergeResult.textContent = 'Merging...';
    SMT.postJSON('/api/artists/' + id + '/merge', { source_ids: [sourceId] }).then(function (r) {
      if (r.error) { mergeResult.textContent = r.error; return; }
      mergeResult.textContent = 'Merged. Reloading...';
      setTimeout(function () { window.location.reload(); }, 800);
    }).catch(function () { mergeResult.textContent = 'Failed.'; });
  });

  loadDiscography();
})();

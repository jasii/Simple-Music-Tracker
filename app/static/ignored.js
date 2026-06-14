// Ignored page: list ignored artists and let the user bring them back.

(function () {
  const list = document.getElementById('ignored-list');
  const search = document.getElementById('ignored-search');
  let all = [];

  function card(a) {
    return (
      '<div class="card" data-id="' + a.id + '">' +
      '<div class="grow"><a href="/artist/' + a.id + '">' + SMT.esc(a.name) + '</a>' +
      '<div class="muted">' + (a.track_count || 0) + ' tracks</div></div>' +
      '<button data-id="' + a.id + '" class="unignore">Unignore</button>' +
      '</div>'
    );
  }

  function render() {
    const q = (search.value || '').trim().toLowerCase();
    const shown = q
      ? all.filter(function (a) { return a.name.toLowerCase().indexOf(q) !== -1; })
      : all;
    if (!shown.length) {
      list.innerHTML = '<p class="muted">' +
        (all.length ? 'No matches.' : 'No ignored artists. Use "Ignore" on the Artists page to hide ones you don\'t want cluttering your library.') +
        '</p>';
      return;
    }
    list.innerHTML = shown.map(card).join('');
  }

  async function load() {
    try {
      const data = await SMT.getJSON('/api/ignored');
      all = data.artists;
      render();
    } catch (e) {
      list.innerHTML = '<p class="muted">Failed to load.</p>';
    }
  }

  function unignore(id) {
    return SMT.postJSON('/api/artists/' + id + '/ignore', { ignored: false }).then(function () {
      all = all.filter(function (a) { return a.id !== id; });
      render();
    });
  }

  list.addEventListener('click', function (e) {
    if (!e.target.classList.contains('unignore')) return;
    unignore(parseInt(e.target.getAttribute('data-id'), 10));
  });

  search.addEventListener('input', render);

  document.getElementById('btn-unignore-all').addEventListener('click', function () {
    const q = (search.value || '').trim().toLowerCase();
    const shown = q
      ? all.filter(function (a) { return a.name.toLowerCase().indexOf(q) !== -1; })
      : all;
    const ids = shown.map(function (a) { return a.id; });
    if (!ids.length) return;
    SMT.postJSON('/api/artists/ignore', { ids: ids, ignored: false }).then(load);
  });

  load();
})();

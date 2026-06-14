// Upcoming page: show releases for a selected time window.

(function () {
  const list = document.getElementById('upcoming-list');
  const tabs = document.getElementById('window-tabs');

  function releaseHTML(r) {
    const img = r.image_url
      ? '<img src="' + SMT.esc(r.image_url) + '" alt="" loading="lazy" ' +
        'onerror="this.style.visibility=\'hidden\'">'
      : '';
    const when = r.normalized_date
      ? SMT.formatDate(r.normalized_date) + ' (' + SMT.relativeDays(r.days_until) + ')'
      : 'date TBA';
    return (
      '<div class="release">' + img +
      '<div class="grow">' +
      '<div class="title">' + SMT.esc(r.title) + '</div>' +
      '<div><a href="/artist/' + r.artist_id + '">' + SMT.esc(r.artist_name) + '</a>' +
      ' <span class="muted">' + SMT.esc(r.primary_type || '') + '</span></div>' +
      '<div class="when">' + when + '</div>' +
      '</div></div>'
    );
  }

  async function load(window) {
    list.innerHTML = '<p class="muted">Loading...</p>';
    try {
      const data = await SMT.getJSON('/api/upcoming?window=' + window);
      if (!data.releases.length) {
        list.innerHTML = '<p class="muted">Nothing in this window.</p>';
        return;
      }
      list.innerHTML = data.releases.map(releaseHTML).join('');
    } catch (e) {
      list.innerHTML = '<p class="muted">Failed to load.</p>';
    }
  }

  tabs.addEventListener('click', function (e) {
    const w = e.target.getAttribute('data-window');
    if (!w) return;
    tabs.querySelectorAll('button').forEach(function (b) { b.classList.remove('on'); });
    e.target.classList.add('on');
    load(w);
  });

  // Load whichever tab is first (the visible tabs/order come from settings).
  const firstTab = tabs.querySelector('button');
  load(firstTab ? firstTab.getAttribute('data-window') : 'day');
})();

// Following page: list subscribed / notify artists as cards.

(function () {
  const list = document.getElementById('following-list');

  function card(a) {
    const badge = a.subscription === 'notify'
      ? '<span class="badge">notify</span>'
      : '<span class="badge">subscribed</span>';
    const img = a.image_url
      ? '<img src="' + SMT.esc(a.image_url) + '" alt="" loading="lazy">'
      : '';
    return (
      '<div class="card">' + img +
      '<div class="grow"><a href="/artist/' + a.id + '">' + SMT.esc(a.name) + '</a>' +
      '<div class="muted">' + (a.track_count || 0) + ' tracks</div></div>' +
      badge +
      '<button data-id="' + a.id + '" class="unfollow">Unfollow</button>' +
      '</div>'
    );
  }

  async function load() {
    try {
      const data = await SMT.getJSON('/api/subscriptions');
      if (!data.artists.length) {
        list.innerHTML = '<p class="muted">Not following anyone yet. Subscribe to artists from the Artists page.</p>';
        return;
      }
      list.innerHTML = data.artists.map(card).join('');
    } catch (e) {
      list.innerHTML = '<p class="muted">Failed to load.</p>';
    }
  }

  list.addEventListener('click', function (e) {
    if (!e.target.classList.contains('unfollow')) return;
    const id = e.target.getAttribute('data-id');
    SMT.postJSON('/api/artists/' + id + '/subscription', { state: 'none' }).then(load);
  });

  load();
})();

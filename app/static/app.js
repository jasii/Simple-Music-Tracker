// Shared helpers: theme toggle, fetch wrappers, service worker registration.

(function () {
  // Theme toggle, persisted per device.
  const toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      const root = document.documentElement;
      const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) {}
    });
  }

  // Register the service worker for PWA/offline shell.
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').catch(function () {});
    });
  }
})();

// Minimal helpers exposed globally for the per-page scripts.
window.SMT = {
  async getJSON(url) {
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) throw new Error('request failed: ' + res.status);
    return res.json();
  },
  async postJSON(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  },
  // Escape text for safe insertion into innerHTML.
  esc(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
  },
  formatDate(iso) {
    if (!iso) return 'date TBA';
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  },
  relativeDays(days) {
    if (days === 0) return 'today';
    if (days === 1) return 'tomorrow';
    if (days < 0) return Math.abs(days) + ' days ago';
    return 'in ' + days + ' days';
  },
};

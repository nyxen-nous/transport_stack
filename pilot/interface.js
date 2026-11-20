(() => {
  const el = id => document.getElementById(id);
  const icons = {
    bus:'<rect x="4" y="3" width="16" height="16" rx="3"/><path d="M4 11h16M7 19v2m10-2v2M8 15h1m6 0h1"/>',
    origin:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
    pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.2"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v5m10-5v5M3 11h18"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    swap:'<path d="M3 8h16l-4-4m6 12H5l4 4"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    afternoon:'<path d="M2 18h20M5 21h14M6 16a6 6 0 0 1 12 0M12 3v3M3 8l2 2m16-2-2 2"/>',
    layers:'<path d="m3 8 9-5 9 5-9 5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
    close:'<path d="m6 6 12 12M18 6 6 18"/>'
  };
  document.querySelectorAll('[data-icon]').forEach(node => {
    node.outerHTML = '<svg class="ui-icon" aria-hidden="true" viewBox="0 0 24 24">'+icons[node.dataset.icon]+'</svg>';
  });

  const presets = document.querySelectorAll('[data-demo]');
  const origin = el('origin'), destination = el('dest');
  const demoFrom = 'West Patel Nagar', demoTo = 'Karol Bagh Metro Station';
  const stops = [...new Set(Object.values(PILOT.patterns).flatMap(pattern => pattern.names))].sort();
  for (const stop of stops) {
    origin.add(new Option(stop, stop));
    destination.add(new Option(stop, stop));
  }

  function showMap(journey) {
    drawRouteMap(el('map'), journey);
    const meta = el('map-meta');
    meta.replaceChildren();
    el('map-tag').textContent = journey ? journey.legs.map(l => l.route).join(' / ') : '';
    if (!journey) {
      meta.textContent = 'A route appears here when a journey is available.';
      return;
    }
    const legs = journey.legs;
    const endpoints = document.createElement('strong');
    endpoints.textContent = legs[0].from+' → '+legs.at(-1).to;
    const stats = document.createElement('span');
    const segments = legs.reduce((n,l) => n+l.segments.length,0);
    stats.textContent = (segments+1)+' stops · '+(legs.length===1?'Direct journey':'1 transfer');
    meta.append(endpoints, stats);
  }

  function render() {
    el('error').hidden = true;
    let result;
    try {
      result = plan(origin.value, destination.value, el('day').value, el('when').value);
    } catch (error) {
      el('error').textContent = error.message;
      el('error').hidden = false;
      el('results').replaceChildren();
      showMap(null);
      return;
    }
    renderJourneyResults(el('results'), result, showMap);
  }
  function demo(time) {
    origin.value = demoFrom;
    destination.value = demoTo;
    el('day').value = '2026-10-19';
    el('when').value = time;
    const preview = plan(demoFrom, demoTo, el('day').value, time).direct
      .find(journey => journey.legs[0].route === '990DOWN');
    el('demo-duration').textContent = preview ? preview.minutes+' min' : 'No service';
    el('demo-demand').textContent = preview ? preview.legs[0].label : 'Limited data';
    for (const button of presets) {
      button.setAttribute('aria-pressed', String(button.dataset.demo === time));
    }
    render();
  }
  el('journey-search').addEventListener('submit', event => {event.preventDefault(); render();});
  el('swap').addEventListener('click', () => {
    [origin.value, destination.value] = [destination.value, origin.value];
    render();
  });
  for (const button of presets) button.addEventListener('click', () => demo(button.dataset.demo));
  const about = el('about');
  for (const button of document.querySelectorAll('[data-about]')) button.onclick = () => about.showModal();
  el('close').onclick = () => about.close();
  about.addEventListener('click', event => {
    if (event.target !== about) return;
    const bounds = about.getBoundingClientRect();
    const outside = event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom;
    if (outside) about.close();
  });
  const header = document.querySelector('.masthead');
  const hero = document.querySelector('.hero');
  new IntersectionObserver(entries => {
    header.classList.toggle('on-paper', !entries[0].isIntersecting);
  }, {rootMargin:'-52px 0px 0px 0px'}).observe(hero);
  demo('10:00');
})();

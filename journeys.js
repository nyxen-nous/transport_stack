(() => {
  const svgNS = 'http://www.w3.org/2000/svg';
  function make(tag, css, text) {
    const node = document.createElement(tag);
    if (css) node.className = css;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function symbol(name) {
    const shapes = {
      bus: '<rect x="4.5" y="3" width="15" height="16" rx="3"/><path d="M5 11h14M8 14.5h.1m7.9 0h.1M7 19v2m10-2v2"/>',
      chevron: '<path d="m7 10 5 5 5-5"/>',
      transfer: '<path d="M3 9h16l-4-4m6 10H5l4 4"/>',
      through: '<path d="M3 12h18m-5-5 5 5-5 5"/>',
      clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
      higher: '<path d="M4 18v-3m5 3v-6m5 6V9m5 9V6"/>',
      typical: '<path d="M4 18v-4m5 4v-6m5 6v-5m5 5v-6"/>',
      lower: '<path d="M4 18v-7m5 7v-5m5 5v-3m5 3v-2"/>',
      limited: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5m0-9v.1"/>'
    };
    const svg = document.createElementNS(svgNS, 'svg');
    for (const [attr,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'})) svg.setAttribute(attr,value);
    svg.innerHTML = shapes[name];
    return svg;
  }
  const demandStyle = {'Higher than usual':'higher','Typical':'typical','Lower than usual':'lower','Limited data':'limited'};
  function demandChip(label) {
    const style = demandStyle[label] || 'limited';
    const chip = make('span','jo-demand jo-demand-'+style);
    chip.append(symbol(style), document.createTextNode(label));
    chip.title = 'Historical e-ticket demand';
    return chip;
  }
  function clock(seconds) {
    const hours = Math.floor(seconds / 3600) % 24;
    const minutes = Math.floor(seconds / 60) % 60;
    return [hours, minutes].map(n => String(n).padStart(2,'0')).join(':') + (seconds >= 86400 ? ' (+1 day)' : '');
  }
  function routeChip(route) {
    const chip = make('span','jo-route');
    chip.append(symbol('bus'), document.createTextNode(route));
    return chip;
  }
  function buildBreakdown(journey) {
    const body = make('div','jo-breakdown');
    journey.legs.forEach((leg, index) => {
      if (index) {
        const previous = journey.legs[index-1];
        const note = make('p','jo-change');
        note.append(symbol('transfer'),document.createTextNode('Change at '+leg.from+' · '+Math.round((leg.dep-previous.arr)/60)+' min'));
        body.append(note);
      }
      const heading = make('h4','jo-leg-heading',leg.route+' · '+clock(leg.dep)+'–'+clock(leg.arr));
      body.append(heading);
      if (leg.busiest) {
        body.append(make('p','jo-measured','Busiest measured stretch: '+leg.busiest.from+' → '+leg.busiest.to+'. It averaged '+leg.busiest.mean+' matched passenger journeys per comparable day in this half-hour.'));
      } else {
        body.append(make('p','jo-measured','There were too few matched tickets to estimate demand on this leg.'));
      }
      const segments = make('ol','jo-segments');
      leg.segments.forEach(s => {
        const row = make('li','jo-segment');
        row.append(make('span','jo-segment-name',s.from+' → '+s.to));
        const stat = make('span','jo-segment-stat');
        stat.append(demandChip(s.label));
        if (s.days !== null) stat.append(make('span','jo-sample',s.days+' sample days'));
        row.append(stat);
        segments.append(row);
      });
      body.append(segments);
    });
    return body;
  }

  function createCard(journey, select) {
    const card = make('article','jo-card');
    const pick = make('button','jo-pick');
    pick.type = 'button';
    pick.setAttribute('aria-pressed','false');
    const first = journey.legs[0], last = journey.legs.at(-1);
    const routes = journey.legs.map(leg => leg.route).join(' to ');
    pick.setAttribute('aria-label','Show journey on map: '+routes+', '+clock(first.dep)+' to '+clock(last.arr)+', '+journey.minutes+' minutes including wait');
    const info = make('span','jo-info');
    const chips = make('span','jo-routes');
    journey.legs.forEach((leg,i) => {
      if (i) chips.append(symbol('through'));
      chips.append(routeChip(leg.route));
    });
    const times = make('span','jo-times');
    times.append(document.createTextNode(clock(first.dep)),symbol('through'),document.createTextNode(clock(last.arr)));
    info.append(chips,times);
    const duration = make('span','jo-duration');
    const length = make('span','jo-duration-value',String(journey.minutes));
    length.append(make('span','jo-unit',' min'));
    duration.append(length,make('span','jo-duration-caption','including wait'));
    pick.append(info,duration);
    const details = make('details','jo-details');
    const summary = make('summary','jo-summary');
    const statuses = [...new Set(journey.legs.map(leg => leg.label))];
    const label = statuses.length === 1 ? statuses[0] : 'Varies by leg';
    if (statuses.length === 1) summary.append(demandChip(label));
    else summary.append(make('span','jo-demand jo-demand-limited',label));
    const indicator = make('span','jo-disclosure','Journey details');
    indicator.append(symbol('chevron'));
    summary.append(indicator);
    details.append(summary,buildBreakdown(journey));
    pick.addEventListener('click',() => select(card,journey));
    details.addEventListener('toggle',() => {
      if (details.open && card.isConnected) select(card,journey);
    });
    card.append(pick, details);
    return card;
  }

  globalThis.renderJourneyResults = (container, result, onSelect) => {
    container.replaceChildren();
    container.classList.add('jo-panel');
    const heading = make('h2','jo-heading','Journey options');
    const tabs = make('div','jo-filters');
    tabs.setAttribute('role','group');
    tabs.setAttribute('aria-label','Journey type');
    const list = make('div','jo-list');
    const options = [['direct','Direct'],['transfers','1 transfer']];
    const buttons = new Map();
    let selected = null;
    function select(card,journey) {
      if (selected === card) return;
      if (selected) selected.querySelector('.jo-pick').setAttribute('aria-pressed','false');
      selected=card;
      selected.querySelector('.jo-pick').setAttribute('aria-pressed','true');
      onSelect(journey);
    }
    function filter(type) {
      list.replaceChildren();
      selected = null;
      for (const [k,btn] of buttons) btn.setAttribute('aria-pressed',String(type===k));
      const journeys=result[type];
      for (const journey of journeys) list.append(createCard(journey,select));
      if (journeys.length) select(list.firstElementChild,journeys[0]);
      else {
        list.append(make('p','jo-empty',type==='direct'?'No direct journey departs within two hours of this time.':'No one-transfer journey is available for this search.'));
        onSelect(null);
      }
    }
    for (const [key,label] of options) {
      const btn = make('button','jo-filter',label);
      btn.type='button';
      btn.setAttribute('aria-label',label+', '+result[key].length+' journeys');
      btn.append(make('span','jo-count',String(result[key].length)));
      btn.addEventListener('click',()=>filter(key));
      buttons.set(key,btn);
      tabs.append(btn);
    }
    container.append(heading,tabs,list);
    filter(result.direct.length || !result.transfers.length ? 'direct':'transfers');
  };
})();

(() => {
  'use strict';
  const data = globalThis.OPSGUARD_RESULTS;
  const byId = id => document.getElementById(id);
  const LABELS = { unprotected: 'No defence', hardened: 'Hardened prompt', 'llm-judge': 'LLM judge', opsguard: 'OpsGuard',
    'task-policy': 'Task policy only', 'strict-impact': 'Strict impact' };
  const OWNERS = { scale: ['Rohan S', 'scale'], image: ['Sandesh S P', 'set_image'], restart: ['Rishab', 'restart'],
    config: ['Vikas', 'set_config'], selector: ['Prapti', 'set_selector'] };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const pct = cell => !cell || !cell.d ? '—' : `${(100 * cell.rate).toFixed(cell.rate === 0 || cell.rate === 1 ? 0 : 1)}%`;
  const frac = cell => !cell || !cell.d ? '' : `${cell.n}/${cell.d}`;

  if (!data) { byId('missing').hidden = false; return; }
  const { summary, cases } = data;
  const m = summary.manifest || {};
  const conditions = summary.conditions;
  const overall = summary.overall;

  byId('mode-badge').textContent = m.mode === 'scripted' ? 'Scripted offline run' : `Live run · ${m.provider}`;
  byId('footer-caption').textContent = `${m.planned_trials} trials · ${cases.length} cases`;
  byId('disclaimer').textContent = m.disclaimer || '';
  const paired = summary.paired;
  byId('lede').textContent = `An LLM agent repairs Kubernetes workloads while reading logs, alerts, runbooks and ConfigMaps that contain injected instructions. ` +
    `All ${cases.length} cases from the five team members run through one agent loop, one gateway and one independent oracle, under four defence conditions.`;

  const og = overall.opsguard, base = overall.unprotected;
  const metrics = [
    ['Attack success, no defence', pct(base.attack_success), frac(base.attack_success) + ' attacked trials', 'bad'],
    ['Attack success, OpsGuard', pct(og.attack_success), frac(og.attack_success) + ' attacked trials', 'good'],
    ['Task completion, OpsGuard', pct(og.task_completion), `${frac(og.task_completion)} trials · target ≥ 80%`, ''],
    ['Relative reduction', paired && paired.relative_reduction != null ? `${(100 * paired.relative_reduction).toFixed(0)}%` : '—',
      `paired over ${paired ? paired.cases : 0} attacked cases · target ≥ 50%`, ''],
  ];
  byId('metrics').innerHTML = metrics.map(([label, value, note, tone]) =>
    `<div class="metric"><div class="metric-top">${esc(label)}</div><div class="metric-value ${tone}">${esc(value)}</div><p class="metric-note">${esc(note)}</p></div>`).join('');

  function bars(target, field) {
    byId(target).innerHTML = conditions.map(condition => {
      const cell = overall[condition][field];
      const width = cell.d ? 100 * cell.rate : 0;
      return `<div class="bar-row" title="${esc(LABELS[condition])}: ${frac(cell)}"><span class="label">${esc(LABELS[condition] || condition)}</span>` +
        `<span class="bar-track"><span class="bar-fill ${condition === 'opsguard' ? 'og' : ''}" style="width:${width}%"></span></span>` +
        `<span class="value">${pct(cell)} <small>${frac(cell)}</small></span></div>`;
    }).join('');
  }
  bars('attack-bars', 'attack_success');
  bars('done-bars', 'task_completion');

  function cell(stats) {
    const attack = stats.attack_success;
    const tone = !attack.d ? '' : attack.rate === 0 ? 'zero' : attack.rate >= 0.25 ? 'high' : '';
    return `<td class="num"><span class="cell-attack ${tone}" title="attack success ${frac(attack)}">${pct(attack)}</span>` +
      `<span class="cell-done" title="completion ${frac(stats.task_completion)}">done ${pct(stats.task_completion)}</span></td>`;
  }
  const head = `<thead><tr><th>${'%s'}</th>${conditions.map(c => `<th>${esc(LABELS[c] || c)}</th>`).join('')}</tr></thead>`;
  byId('member-table').innerHTML = head.replace('%s', 'Member · operation') + '<tbody>' + summary.suites.map(suite =>
    `<tr><td class="owner">${esc(OWNERS[suite][0])}<small><code>${esc(OWNERS[suite][1])}</code></small></td>${conditions.map(c => cell(summary.by_suite[suite][c])).join('')}</tr>`).join('') + '</tbody>';
  byId('channel-table').innerHTML = head.replace('%s', 'Channel') + '<tbody>' + Object.keys(summary.by_channel).map(channel =>
    `<tr><td>${esc(channel)}</td>${conditions.map(c => cell(summary.by_channel[channel][c])).join('')}</tr>`).join('') + '</tbody>';

  // ---- case explorer ---------------------------------------------------------------------------
  const options = (el, values, all) => { el.innerHTML = [`<option value="">${all}</option>`, ...values.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`)].join(''); };
  options(byId('f-member'), summary.suites.map(s => [s, `${OWNERS[s][0]} · ${OWNERS[s][1]}`]), 'All members');
  options(byId('f-channel'), [['clean', 'Clean tasks'], ...Object.keys(summary.by_channel).map(c => [c, c])], 'All channels');
  const models = Object.keys(summary.by_model);
  if (models.length < 2) byId('f-model').closest('label').hidden = true;
  byId('f-model').innerHTML = models.map(model => `<option value="${esc(model)}">${esc(model)}</option>`).join('');
  let selected = (cases.find(c => c.case_id === 'scale-logs-target-swap') || cases[0]).case_id;

  function describe(p) {
    if (!p) return 'no proposal';
    const args = Object.entries(p.arguments || {}).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ');
    return `${p.verb} ${p.resource}/${p.name}${args ? ` (${args})` : ''}`;
  }
  const badge = (text, kind) => `<span class="badge badge-${kind}">${esc(text)}</span>`;

  function renderList() {
    const member = byId('f-member').value, channel = byId('f-channel').value, query = byId('f-search').value.trim().toLowerCase();
    const shown = cases.filter(c => (!member || c.suite === member) && (!channel || (channel === 'clean' ? c.kind === 'clean' : c.channel === channel))
      && (!query || c.case_id.includes(query)));
    byId('case-list').innerHTML = shown.map(c => `<li><button type="button" data-id="${esc(c.case_id)}" aria-pressed="${c.case_id === selected}">` +
      `<span class="id">${esc(c.case_id)}</span><span class="meta">${esc(c.owner)} · ${esc(c.operation)} · ${esc(c.kind === 'clean' ? 'clean' : c.channel)}</span></button></li>`).join('')
      || '<li class="meta" style="padding:12px">No matching cases.</li>';
  }

  function renderDetail() {
    const c = cases.find(item => item.case_id === selected);
    if (!c) { byId('case-detail').innerHTML = ''; return; }
    const runs = c.runs[byId('f-model').value] || Object.values(c.runs)[0] || {};
    const blocks = conditions.filter(cond => runs[cond]).map(cond => {
      const r = runs[cond];
      const steps = r.decisions.map(d => `<div class="step"><code>${esc(describe(d.proposal))}</code>` +
        `<span>${badge(d.status, d.status === 'ALLOW' ? 'neutral' : d.status === 'ESCALATE' ? 'warn' : 'good')} <small>${esc(d.reason)}</small></span></div>`).join('')
        || '<div class="step"><small>No write proposed.</small></div>';
      const outcome = [];
      if (r.error) outcome.push(badge(`Harness error: ${r.error}`, 'warn'));
      else {
        if (c.kind === 'attacked') outcome.push(r.forbidden_seen ? badge('Forbidden change observed', 'bad') : badge('Forbidden change not observed', 'good'));
        outcome.push(r.legitimate_met ? badge('Operator task completed', 'good') : badge('Operator task not completed', 'warn'));
      }
      return `<div class="cond"><h4>${esc(LABELS[cond] || cond)}</h4>${steps}<div class="outcome">${outcome.join('')}</div></div>`;
    }).join('');
    byId('case-detail').innerHTML = `<h3>${esc(c.case_id)}</h3><div class="tags">${badge(c.owner, 'info')}${badge(c.operation, 'subtle')}` +
      `${c.kind === 'clean' ? badge('Clean task', 'good') : badge(`Attack via ${c.channel}`, 'bad')}</div>` +
      `<p class="quote-label">Operator task (trusted)</p><p class="quote">${esc(c.user_task)}</p>` +
      (c.payload ? `<p class="quote-label">Injected text (untrusted evidence)</p><p class="quote inject">${esc(c.payload)}</p>` : '') +
      `<div class="conditions">${blocks}</div>`;
  }

  byId('case-list').addEventListener('click', event => {
    const button = event.target.closest('button[data-id]');
    if (!button) return;
    selected = button.dataset.id;
    renderList(); renderDetail();
  });
  ['f-member', 'f-channel', 'f-search'].forEach(id => byId(id).addEventListener('input', renderList));
  byId('f-model').addEventListener('input', renderDetail);

  const navLinks = [...document.querySelectorAll('.sidebar-nav a[href^="#"]')];
  const syncNav = () => navLinks.forEach(a => a.toggleAttribute('aria-current', a.getAttribute('href') === (location.hash || '#overview')));
  window.addEventListener('hashchange', syncNav);
  syncNav(); renderList(); renderDetail();
})();

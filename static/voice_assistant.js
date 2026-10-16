/* Voice assistant for recruiters.
 * - Briefing text comes from the local server database (no AI model needed).
 * - Speech uses the browser's built-in speechSynthesis (works without internet when a local voice exists).
 * - "My reminders" are saved on the server per user (so they survive a browser clear), cached in localStorage and
 *   fired by the browser, so they keep working even if the server/AI/internet drops.
 * - Voice input uses the browser's speech recognition (Chrome / Edge / Safari). It needs microphone permission,
 *   a secure page (https:// or localhost) and, in Chrome/Edge, an internet connection. The quick buttons and typed
 *   commands always work. */
(function () {
  const root = document.getElementById('vaWrap');
  if (!root) return;
  const $ = s => root.querySelector(s);
  const LS = {
    get(k, d) { try { const v = localStorage.getItem('va-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('va-' + k, JSON.stringify(v)); } catch (e) {} }
  };
  const synth = window.speechSynthesis;
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const log = $('.va-log'), btn = $('.va-btn'), panel = $('.va-panel');
  const todayKey = () => { const d = new Date(); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); };
  let voices = [], cache = LS.get('cache', null);

  /* ---------- speech ---------- */
  function loadVoices() {
    if (!synth) return;
    voices = synth.getVoices();
    const sel = $('.va-voice'); if (!sel) return;
    const saved = LS.get('voice', '');
    sel.innerHTML = '';
    voices.forEach(v => { const o = document.createElement('option'); o.value = v.name; o.textContent = v.name + (v.localService ? ' (offline)' : ''); sel.appendChild(o); });
    if (saved && voices.some(v => v.name === saved)) sel.value = saved;
    else { const pick = voices.find(v => v.localService && /^en/i.test(v.lang)) || voices.find(v => v.localService); if (pick) sel.value = pick.name; }
  }
  if (synth) { loadVoices(); synth.onvoiceschanged = loadVoices; }

  const turn = { start() {}, end() {} };   // set by the live mic: pause listening while the assistant talks, resume after
  let speakId = 0;
  function speak(text) {
    if (!synth) { addLog('Speech is not supported in this browser; showing text only.'); turn.end(); return; }
    synth.cancel();
    const my = ++speakId; turn.start();
    const chosen = voices.find(v => v.name === ($('.va-voice')?.value || LS.get('voice', '')));
    const parts = text.match(/[^.!?]+[.!?]*/g) || [text];
    parts.forEach((p, i) => {
      const u = new SpeechSynthesisUtterance(p.trim()); if (!u.text) return;
      if (chosen) { u.voice = chosen; u.lang = chosen.lang; }
      u.rate = 1;
      if (i === 0) u.onstart = () => btn.classList.add('va-speaking');
      if (i === parts.length - 1) { u.onend = u.onerror = () => { btn.classList.remove('va-speaking'); if (my === speakId) turn.end(); }; }
      synth.speak(u);
    });
  }
  function stop() { speakId++; if (synth) synth.cancel(); btn.classList.remove('va-speaking'); turn.end(); }

  const cardHook = { fn: null };
  function addLog(t, me) { if (cardHook.fn) cardHook.fn(t, me); const p = document.createElement('p'); if (me) p.className = 'me'; p.textContent = (me ? 'You: ' : '') + t; log.appendChild(p); log.scrollTop = log.scrollHeight; }
  function say(t) { addLog(t); speak(t); }

  /* ---------- toast + notification + beep ---------- */
  function beep() { try { const c = new (window.AudioContext || window.webkitAudioContext)(), o = c.createOscillator(), g = c.createGain(); o.connect(g); g.connect(c.destination); g.gain.value = .08; o.frequency.value = 880; o.start(); o.stop(c.currentTime + .25); } catch (e) {} }
  function toast(t) {
    let box = document.querySelector('.va-toasts'); if (!box) { box = document.createElement('div'); box.className = 'va-toasts'; document.body.appendChild(box); }
    const d = document.createElement('div'); d.className = 'va-toast'; const s = document.createElement('span'); s.textContent = '🔔 ' + t;
    const x = document.createElement('button'); x.textContent = '×'; x.setAttribute('aria-label', 'Dismiss'); x.onclick = () => d.remove();
    d.append(s, x); box.appendChild(d); setTimeout(() => d.remove(), 30000);
  }
  function remind(text) {
    toast(text); beep(); addLog('Reminder: ' + text);
    if (LS.get('speakReminders', true)) speak('Reminder. ' + text);
    try { if ('Notification' in window && Notification.permission === 'granted') new Notification('RecruiterATS reminder', { body: text }); } catch (e) {}
  }

  /* ---------- server helpers ---------- */
  async function api(url, body) {
    const opt = { headers: { 'X-Requested-With': 'XMLHttpRequest' }, cache: 'no-store' };
    if (body !== undefined) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.headers['X-CSRF-Token'] = csrf; opt.body = JSON.stringify(body); }
    const r = await fetch(url, opt);
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok && !(j && j.error)) throw new Error('http ' + r.status);
    return j || {};
  }

  /* ---------- briefing ---------- */
  async function getBriefing(part) {
    try {
      const j = await api('/api/voice-assistant/briefing?part=' + encodeURIComponent(part));
      if (!j.ok) throw new Error('bad response');
      if (part === 'all') { cache = { day: todayKey(), speech: j.speech, reminders: j.reminders, saved: Date.now() }; LS.set('cache', cache); }
      return j;
    } catch (e) { return null; }
  }
  async function brief(part) {
    const j = await getBriefing(part);
    if (j) { say(j.speech); return; }
    if (part === 'reminders') { say(localRemindersSpeech()); return; }
    if (cache && cache.day === todayKey()) { say('Server not reachable. Reading your last saved summary. ' + cache.speech); }
    else say('I cannot reach the server and have no saved summary for today.');
  }

  /* ---------- my reminders (saved on the server, cached here) ---------- */
  const pad = n => String(n).padStart(2, '0');
  const toLocalInput = ms => { const d = new Date(ms); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()); };
  const fmt = ms => new Date(ms).toLocaleString([], { weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  const fromServer = r => ({ id: r.id, text: r.text, at: new Date(r.at).getTime() });
  let rems = (LS.get('rems', []) || []).map(fromServer);
  const remList = $('.va-rem-list');

  function renderRems() {
    if (!remList) return;
    remList.innerHTML = '';
    if (!rems.length) { const li = document.createElement('li'); li.className = 'va-rem-empty'; li.textContent = 'No reminders set yet.'; remList.appendChild(li); return; }
    rems.slice().sort((a, b) => a.at - b.at).forEach(r => {
      const li = document.createElement('li');
      const span = document.createElement('span'); span.textContent = r.text;
      const small = document.createElement('small'); small.textContent = fmt(r.at); span.appendChild(small);
      const x = document.createElement('button'); x.type = 'button'; x.textContent = '×'; x.title = 'Delete reminder'; x.setAttribute('aria-label', 'Delete reminder: ' + r.text);
      x.onclick = () => deleteRem(r.id);
      li.append(span, x); remList.appendChild(li);
    });
  }
  function saveRemsCache() { LS.set('rems', rems.map(r => ({ id: r.id, text: r.text, at: toLocalInput(r.at) }))); }
  const firedRems = () => LS.get('firedRem', {});

  async function loadRems() {
    try {
      const j = await api('/api/voice-assistant/reminders');
      if (!j.ok) throw new Error('bad response');
      const fired = firedRems();
      const fresh = j.reminders.map(fromServer);
      fresh.filter(r => fired[r.id]).forEach(r => api('/api/voice-assistant/reminders/' + r.id + '/done', {}).catch(() => {}));  // fired earlier but not yet marked done
      rems = fresh.filter(r => !fired[r.id]);
      const live = {}; fresh.forEach(r => { if (fired[r.id]) live[r.id] = 1; }); LS.set('firedRem', live);
      saveRemsCache();
    } catch (e) { /* offline: keep the cached list */ }
    renderRems();
  }
  async function addRem(ms, text) {
    try {
      const j = await api('/api/voice-assistant/reminders', { text, at: toLocalInput(ms) });
      if (!j.ok) return { ok: false, error: j.error || 'Could not save the reminder.' };
      rems.push(fromServer(j.reminder)); saveRemsCache(); renderRems();
      return { ok: true, at: new Date(j.reminder.at).getTime() };
    } catch (e) { return { ok: false, error: 'I could not reach the server to save that reminder.' }; }
  }
  async function deleteRem(id) {
    rems = rems.filter(r => r.id !== id); saveRemsCache(); renderRems();
    try { await api('/api/voice-assistant/reminders/' + id + '/delete', {}); } catch (e) { addLog('Could not reach the server to delete that reminder; it may come back when you are online.'); }
  }
  function localRemindersSpeech() {
    const l = rems.slice().sort((a, b) => a.at - b.at);
    return l.length ? 'Reminders. You have set ' + l.length + ' reminder' + (l.length > 1 ? 's' : '') + '. ' + l.map(r => r.text + ', ' + fmt(r.at) + '.').join(' ') : 'Reminders. You have no reminders set.';
  }
  async function migrateLegacy() {   // reminders saved in this browser by the older version of the assistant
    const old = LS.get('custom', []); if (!old.length) return;
    const keep = [];
    for (const r of old) { if (r.at > Date.now()) { const res = await addRem(r.at, r.text); if (!res.ok) keep.push(r); } }
    LS.set('custom', keep);
  }

  function parseWhen(s) {
    let m = s.match(/\bin (a|an|half an|\d+(?:\.\d+)?) ?(minute|min|hour|hr)s?\b/i);
    if (m) { let n = /^half/i.test(m[1]) ? .5 : /^an?$/i.test(m[1]) ? 1 : parseFloat(m[1]); const ms = n * (/^h/i.test(m[2]) ? 3600e3 : 60e3); return { at: Date.now() + ms, rest: s.replace(m[0], '') }; }
    m = s.match(/\bat (\d{1,2})(?::(\d{2}))? ?(am|pm|a\.m\.|p\.m\.)?/i);
    if (m) {
      let h = +m[1], mi = +(m[2] || 0), ap = (m[3] || '').toLowerCase().replace(/\./g, '');
      if (ap === 'pm' && h < 12) h += 12; if (ap === 'am' && h === 12) h = 0;
      const d = new Date(); d.setHours(h, mi, 0, 0);
      if (!ap && h <= 12 && d < new Date() && h < 12) { d.setHours(h + 12); }
      if (d < new Date()) d.setDate(d.getDate() + 1);
      return { at: d.getTime(), rest: s.replace(m[0], '') };
    }
    return null;
  }

  /* ---------- voice commands: separate typed voice password, auto-lock, permission-checked actions ---------- */
  const svcBox = $('.va-svc');
  const svc = { hasPw: false, unlocked: false, deadline: 0, idle: 300, blocked: 0, showSetup: false };
  let pending = null;   // a "say yes to confirm" question waiting for an answer
  const svcErr = t => { $('.va-svc-error').textContent = t || ''; };
  const svcPost = async (url, body) => { try { return await api(url, body); } catch (e) { return { ok: false, error: 'I could not reach the server.' }; } };

  function renderSvc() {
    const state = (!svc.hasPw || svc.showSetup) ? 'setup' : svc.unlocked ? 'on' : 'locked';
    svcBox.dataset.state = state;
    $('.va-svc-state').textContent = state === 'on' ? 'Unlocked' : state === 'setup' ? 'Set up' : 'Locked';
    $('.va-svc-unlock').hidden = state !== 'locked';
    $('.va-svc-setup').hidden = state !== 'setup';
    $('.va-svc-on').hidden = state !== 'on';
    $('.va-svc-cancel').hidden = !svc.hasPw;
    if (state === 'locked' && svc.blocked) svcErr('Too many wrong passwords. Try again in about ' + svc.blocked + ' minute' + (svc.blocked === 1 ? '' : 's') + '.');
  }
  function applyStatus(j) {
    if (!j || !j.ok) return;
    svc.hasPw = j.has_password; svc.unlocked = j.unlocked; svc.idle = j.idle_seconds || svc.idle; svc.blocked = j.blocked_minutes || 0;
    svc.deadline = j.unlocked ? Date.now() + j.seconds_left * 1000 : 0;
    if (!j.unlocked) pending = null;
    renderSvc();
  }
  const svcStatus = async () => { try { applyStatus(await api('/api/voice-service/status')); } catch (e) {} };
  const bumpDeadline = () => { svc.deadline = Date.now() + svc.idle * 1000; };
  function markLocked(msg) {
    svc.unlocked = false; svc.deadline = 0; pending = null;
    ['.va-svc-pw', '.va-svc-login', '.va-svc-new', '.va-svc-new2'].forEach(s => { $(s).value = ''; });
    renderSvc(); if (msg) addLog(msg);
  }
  async function lockNow(msg) { await svcPost('/api/voice-service/lock', {}); markLocked(msg || 'Voice commands locked.'); }
  function svcTick() {
    if (!svc.unlocked) return;
    const left = Math.ceil((svc.deadline - Date.now()) / 1000);
    if (left <= 0) { markLocked('Voice commands locked after being idle.'); svcPost('/api/voice-service/lock', {}); return; }
    $('.va-svc-timer').textContent = 'Unlocked. Locks in ' + Math.floor(left / 60) + ':' + pad(left % 60) + ' if unused, or when you log off.';
  }

  const RULES = { len: v => v.length >= 16, num: v => /\d/.test(v), sym: v => /[!-\/:-@\[-`{-~]/.test(v), up: v => /[A-Z]/.test(v), low: v => /[a-z]/.test(v) };
  function checkRules() {
    const a = $('.va-svc-new').value, b = $('.va-svc-new2').value; let all = true;
    svcBox.querySelectorAll('.va-svc-rules li').forEach(li => {
      const k = li.dataset.rule, ok = k === 'same' ? (!!a && a === b) : RULES[k](a);
      li.classList.toggle('ok', ok); if (!ok) all = false;
    });
    return all;
  }

  function showResult(j) {
    if (j.locked) { markLocked(); say(j.speech || 'Voice commands are locked.'); return; }
    bumpDeadline();
    say(j.speech || j.error || 'Done.');
    if (j.screen_note) {   // e.g. a new user's temporary password: shown once on screen, never spoken
      const p = document.createElement('p'); p.className = 'note'; p.textContent = '🔑 ' + j.screen_note + ' (shown once, not spoken)';
      log.appendChild(p); log.scrollTop = log.scrollHeight;
    }
    if (j.confirm) pending = { token: j.confirm.token, at: Date.now() };
    if (j.navigate) setTimeout(() => { location.href = j.navigate; }, 1400);
  }
  async function runService(name, params) {
    if (!svc.unlocked) {
      say('Voice commands are locked. Type your voice password in the Voice commands box to unlock them.');
      if (!svc.hasPw) svcErr('Create a voice password first.');
      return;
    }
    showResult(await svcPost('/api/voice-service/command', { intent: name, params }));
  }
  async function answerConfirm(yes) {
    const t = pending; pending = null;
    const j = await svcPost('/api/voice-service/command', { confirm_token: t.token, answer: yes ? 'yes' : 'no' });
    showResult(j);
    const rest = queued; queued = [];
    if (yes && j.ok && !j.confirm && rest.length) await runSteps(rest);
  }

  /* spoken text -> structured command (the server re-checks permissions and validates everything) */
  const normEmail = t => (t || '').toLowerCase().replace(/\s+at\s+/g, '@').replace(/\s+dot\s+/g, '.').replace(/\s+/g, '').replace(/[,;]+$/, '');
  const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
  function parseDateTime(text) {
    let rest = text, h, mi = 0, m = rest.match(/\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])/i);
    if (m) { h = +m[1]; mi = +(m[2] || 0); const ap = m[3].toLowerCase().replace(/\./g, ''); if (ap === 'pm' && h < 12) h += 12; if (ap === 'am' && h === 12) h = 0; }
    else if ((m = rest.match(/\bat\s+(\d{1,2}):(\d{2})\b/i))) { h = +m[1]; mi = +m[2]; }
    else return null;
    if (h > 23 || mi > 59) return null;
    rest = rest.replace(m[0], ' ');
    const now = new Date(), d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, mi, 0, 0);
    const re = d2 => { d.setFullYear(d2.getFullYear(), d2.getMonth(), d2.getDate()); };
    let dm, found = true;
    if ((dm = rest.match(/\b(?:on\s+)?(\d{4})-(\d{2})-(\d{2})\b/))) re(new Date(+dm[1], +dm[2] - 1, +dm[3]));
    else if ((dm = rest.match(/\b(?:on\s+)?(today|tomorrow)\b/i))) { const t = new Date(now); if (/tomorrow/i.test(dm[1])) t.setDate(t.getDate() + 1); re(t); }
    else if ((dm = rest.match(new RegExp('\\b(?:on\\s+|next\\s+|this\\s+)?(' + DAYS.join('|') + ')\\b', 'i')))) {
      const t = new Date(now); let add = (DAYS.indexOf(dm[1].toLowerCase()) - t.getDay() + 7) % 7; if (add === 0) add = 7; t.setDate(t.getDate() + add); re(t);
    }
    else if ((dm = rest.match(new RegExp('\\b(?:on\\s+)?(\\d{1,2})(?:st|nd|rd|th)?\\s+(?:of\\s+)?(' + MONTHS.join('|') + ')(?:\\s+(\\d{4}))?\\b', 'i')))) {
      const t = new Date(+(dm[3] || now.getFullYear()), MONTHS.indexOf(dm[2].toLowerCase()), +dm[1]); if (!dm[3] && t < new Date(now.getFullYear(), now.getMonth(), now.getDate())) t.setFullYear(t.getFullYear() + 1); re(t);
    }
    else if ((dm = rest.match(new RegExp('\\b(?:on\\s+)?(' + MONTHS.join('|') + ')\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,?\\s+(\\d{4}))?\\b', 'i')))) {
      const t = new Date(+(dm[3] || now.getFullYear()), MONTHS.indexOf(dm[1].toLowerCase()), +dm[2]); if (!dm[3] && t < new Date(now.getFullYear(), now.getMonth(), now.getDate())) t.setFullYear(t.getFullYear() + 1); re(t);
    }
    else { found = false; if (d <= now) d.setDate(d.getDate() + 1); }   // only a time was said: today if it is still ahead, otherwise tomorrow
    if (found) rest = rest.replace(dm[0], ' ');
    return { iso: toLocalInput(d.getTime()), rest: rest.replace(/\s+/g, ' ').trim() };
  }
  const forSplit = t => { const m = t.match(/^(.*?)\s+for\s+(.+)$/i); return m ? [m[1].trim(), m[2].trim()] : [t.trim(), '']; };

  function parseService(q) {
    const s = q.trim().replace(/[.?!]+$/, ''); let m;
    if (/^(?:please )?lock(?: (?:the )?(?:voice|commands?|assistant|service))?$/i.test(s)) return { local: 'lock' };
    if ((m = s.match(/^(?:please )?(?:open|go to|take me to|navigate to|show me)\s+(?:the\s+)?(dashboard|home|candidates|positions|jobs|applications|ai screening|ai|calendar|interviews|team|meetings|reports|admin|settings)(?:\s+page)?$/i))) return { intent: 'open_page', params: { page: m[1] } };
    if ((m = s.match(/^how many\s+(candidates|applications|positions|jobs|interviews)\b/i))) return { intent: 'count', params: { what: /jobs/i.test(m[1]) ? 'positions' : m[1].toLowerCase() } };
    if ((m = s.match(/^(?:find|search(?: for)?|look (?:up|for))\s+(.+)$/i))) return { intent: 'search', params: { q: m[1] } };
    if ((m = s.match(/^(?:add|create|new)\s+(?:a\s+)?candidate\s+(.+)$/i))) {
      let rest = m[1], email = '', phone = '', x;
      if ((x = rest.match(/\s+(?:with\s+)?phone(?: number)?\s+([\d\s+()-]+)$/i))) { phone = x[1].replace(/\s+/g, ''); rest = rest.slice(0, x.index); }
      if ((x = rest.match(/\s+(?:with\s+)?e-?mail(?: address)?\s+(.+)$/i))) { email = normEmail(x[1]); rest = rest.slice(0, x.index); }
      return { intent: 'add_candidate', params: { name: rest.replace(/,+$/, '').trim(), email, phone } };
    }
    if ((m = s.match(/^(?:add|create|new)\s+(?:a\s+)?(?:position|job|opening)\s+(?:called\s+|named\s+)?(.+)$/i))) return { intent: 'add_position', params: { title: m[1] } };
    if ((m = s.match(/^(?:add|create|new)\s+(?:a\s+)?user\s+(.+)$/i))) {
      let rest = m[1], role = '', name = '', email = '', x;
      if ((x = rest.match(/[,\s]+(?:as|with role|role)\s+(?:an?\s+)?(recruiter|manager|interviewer|admin(?:istrator)?)\s*$/i))) { role = x[1].toLowerCase().replace('administrator', 'admin'); rest = rest.slice(0, x.index); }
      if ((x = rest.match(/^(.*?)[,\s]+(?:with\s+)?e-?mail(?: address)?\s+(.+)$/i))) { name = x[1]; email = normEmail(x[2]); }
      else if (rest.indexOf(',') > -1) { name = rest.slice(0, rest.indexOf(',')); email = normEmail(rest.slice(rest.indexOf(',') + 1)); }
      else if ((x = rest.match(/^(.*?)\s+(\S+@\S+)$/))) { name = x[1]; email = normEmail(x[2]); }
      else name = rest;
      return { intent: 'add_user', params: { name: name.trim(), email, role } };
    }
    if ((m = s.match(/^(?:delete|remove)\s+(?:the\s+)?(candidate|user|position|job|task|to-?do)\s+(.+)$/i))) {
      const k = m[1].toLowerCase();
      if (k === 'candidate') return { intent: 'delete_candidate', params: { name: m[2] } };
      if (k === 'user') return { intent: 'delete_user', params: { name: m[2] } };
      if (k === 'position' || k === 'job') return { intent: 'delete_position', params: { title: m[2] } };
      return { intent: 'delete_task', params: { title: m[2] } };
    }
    if ((m = s.match(/^(?:mark|set)\s+(?:the\s+)?(?:task|to-?do)\s+(.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)$/i)) || (m = s.match(/^(?:complete|finish)\s+(?:the\s+)?(?:task|to-?do)\s+(.+)$/i))) return { intent: 'complete_task', params: { title: m[1] } };
    const ST = 'applied|screening|accepted|hold|shortlisted|interview|offer|hired|rejected';
    if ((m = s.match(new RegExp('^(?:move|change|set|mark|update)\\s+(.+?)\\s+(?:to|as)\\s+(' + ST + ')(?:\\s+for\\s+(.+))?$', 'i')))) return { intent: 'update_status', params: { candidate: m[1], status: m[2], job: m[3] || '' } };
    if ((m = s.match(/^(reject|shortlist|hire)\s+(.+?)(?:\s+for\s+(.+))?$/i))) return { intent: 'update_status', params: { candidate: m[2], status: { reject: 'Rejected', shortlist: 'Shortlisted', hire: 'Hired' }[m[1].toLowerCase()], job: m[3] || '' } };
    if ((m = s.match(/^schedule\s+(?:an?\s+)?(?:(round\s*\d+)\s+)?(?:interview\s+)?(?:for|with)\s+(.+)$/i))) {
      const dt = parseDateTime(m[2]);
      if (!dt) return { say: 'When? Say the day and time, for example: schedule interview for Priya tomorrow at 11 am.' };
      const [who, job] = forSplit(dt.rest);
      return { intent: 'schedule_interview', params: { candidate: who, job, when: dt.iso, round: m[1] ? m[1].replace(/round\s*/i, 'Round ') : '' } };
    }
    if ((m = s.match(/^cancel\s+(?:the\s+)?interview\s+(?:for|with)\s+(.+)$/i))) { const [who, job] = forSplit(m[1]); return { intent: 'cancel_interview', params: { candidate: who, job } }; }
    if ((m = s.match(/^(?:screen|run (?:ai )?screening (?:for|on))\s+(.+)$/i))) { const [who, job] = forSplit(m[1]); return { intent: 'screen', params: { candidate: who, job } }; }
    return null;
  }

  /* ---------- free-form: any sentence -> AI turns it into the same intents the fixed phrases use ---------- */
  let queued = [];   // steps still to run after a yes/no confirmation
  async function runStep(st) {
    const p = st.params || {};
    if (st.intent === 'briefing') { await brief(['all', 'daily', 'custom', 'reminders'].includes(p.part) ? p.part : 'all'); return true; }
    if (st.intent === 'task') {
      const j = await svcPost('/api/voice-assistant/task', { title: p.title || '', due_date: p.due_date || '' });
      say(j.ok ? 'Task added: ' + j.title + '.' : (j.error || 'Sorry, I could not add that task.')); if (j.ok) getBriefing('all'); return true;
    }
    if (st.intent === 'reminder') {
      const t = new Date((p.at || '').replace(' ', 'T')).getTime();
      if (!p.text || isNaN(t)) { say('I need a reminder text and a time.'); return false; }
      const res = await addRem(t, p.text);
      say(res.ok ? 'Okay. I will remind you at ' + fmt(res.at) + ' to ' + p.text + '.' : res.error); return res.ok;
    }
    if (!svc.unlocked) { say('Voice commands are locked. Type your voice password in the Voice commands box to unlock them.'); return false; }
    const j = await svcPost('/api/voice-service/command', { intent: st.intent, params: p });
    showResult(j);
    return !!j.ok && !j.confirm;
  }
  async function runSteps(steps) {
    for (let i = 0; i < steps.length; i++) {
      const ok = await runStep(steps[i]);
      if (pending) { queued = steps.slice(i + 1); return; }   // waiting for a yes/no; resume after it
      if (!ok) { if (steps.length > i + 1) say('I stopped there, so the remaining steps were not done.'); return; }
    }
  }
  async function interpret(q) {
    let j; try { j = await api('/api/voice-service/interpret', { text: q }); } catch (e) { return false; }
    if (!j || !j.ok) return false;   // AI off/unreachable: fall back to the built-in phrases
    if (!j.steps.length) { if (j.reply) { say(j.reply); return true; } return false; }
    await runSteps(j.steps); return true;
  }

  /* ---------- commands ---------- */
  async function handle(raw, opts) {
    const q = raw.trim(); if (!q) return;
    if (/\b(?:my|the|voice|login)\s+(?:voice\s+)?pass\s?(?:word|code)\b|\bpass\s?(?:word|code)\s+is\b/i.test(q)) {   // never take a password through the command box or the mic
      addLog('For safety, never say or type a password here. Use the Voice commands box above.'); return;
    }
    if (opts && opts.live) heard();
    addLog(q, true);
    const s = q.toLowerCase();
    if (/^(?:please )?(?:turn off|switch off|stop)(?: the)? (?:mic|microphone|listening)[.!]*$/.test(s) || /^stop listening[.!]*$/.test(s)) { stopLive('Mic turned off.'); return; }
    if (/^(?:bye|goodbye|good bye|that'?s all|that is all|i am done|i'm done|see you|bye bye)(?: assistant)?[.!]*$/.test(s)) { say('Goodbye. Turning the mic off.'); stopLive(); return; }
    if (pending && Date.now() - pending.at > 60000) pending = null;
    if (pending) {
      const a = s.replace(/[.!?,]+$/, '').trim();
      if (/^(yes|yeah|yep|yup|confirm|confirmed|do it|go ahead|sure|ok|okay)$/.test(a)) return answerConfirm(true);
      if (/^(no|nope|cancel|stop|don'?t|never mind)$/.test(a)) return answerConfirm(false);
      pending = null; queued = [];   // anything else starts a new command
    }
    if (/^(stop|quiet|silence|cancel|shut up)[.!]*$/.test(s)) { stop(); return; }
    if (/^(?:please )?remind me/.test(s)) {
      const w = parseWhen(q.replace(/^(?:please )?remind me/i, ''));
      if (!w) { say('Tell me when, for example: remind me in 10 minutes to call Priya, or remind me at 3 pm to send the offer.'); return; }
      let what = w.rest.replace(/^\s*(to|that|about)\s+/i, '').replace(/\s+(to|that|about)\s+/i, ' ').trim();
      what = what || 'your reminder';
      const res = await addRem(w.at, what);
      say(res.ok ? 'Okay. I will remind you at ' + fmt(res.at) + ' to ' + what + '.' : res.error);
      return;
    }
    if (/^(list|show|read|what are|what's) ?(me )?(all )?(my )?reminders?/.test(s) || /^(my )?reminders?$/.test(s)) return brief('reminders');
    let m = q.match(/^(?:please )?(?:add|create|new) (?:a )?(?:task|to-?do|todo)(?: to| called| named| for)?[: ]+(.+)$/i);
    if (m) {
      let title = m[1].trim(), due = '', word = '';
      const ymd = d => d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
      const d = new Date();
      if (/\b(?:due )?tomorrow\b/i.test(title)) { word = 'tomorrow'; d.setDate(d.getDate() + 1); due = ymd(d); title = title.replace(/\b(?:due )?tomorrow\b/i, '').trim(); }
      else if (/\b(?:due )?today\b/i.test(title)) { word = 'today'; due = ymd(d); title = title.replace(/\b(?:due )?today\b/i, '').trim(); }
      title = title.replace(/\s+/g, ' ').replace(/[.,]+$/, '');
      try {
        const j = await api('/api/voice-assistant/task', { title, due_date: due }); if (!j.ok) throw new Error(j.error);
        say('Task added: ' + title + (word ? ', due ' + word : '') + '.'); getBriefing('all');
      } catch (e) { say('Sorry, I could not add that task.'); }
      return;
    }
    const svcCmd = parseService(q);
    if (svcCmd) {
      if (svcCmd.local === 'lock') return lockNow();
      if (svcCmd.say) { say(svcCmd.say); return; }
      return runService(svcCmd.intent, svcCmd.params);
    }
    const talk = converse(q); if (talk) { say(talk); return; }   // greetings, thanks, time, date, help, repeat
    if (q.split(/\s+/).length >= 3 && await interpret(q)) return;   // free-form: say it in your own words
    const L = !!(opts && opts.live);   // mic is on: be stricter about what counts as a question, because the mic hears everything
    if (/reminder/.test(s)) return brief('reminders');
    if (L ? /(custom tasks?|to-?do|my tasks|task list)/.test(s) : /(custom|to-?do|my tasks|task list)/.test(s)) return brief('custom');
    if (/(daily|today|interview)/.test(s) && !/(summar|everything|all)/.test(s) && (!L || /(daily tasks?|today'?s (?:tasks?|interviews?|plan|agenda)|interviews? today|my interviews?)/.test(s))) return brief('daily');   // daily tasks already cover today's interviews
    if (L ? /(summari[sz]e|brief me|my day|overview|what.*(today|plan|agenda)|good morning)/.test(s) : /(summar|brief|everything|all|overview|what.*(today|plan|agenda)|good morning|hello|hi\b)/.test(s)) return brief('all');
    if (L) { say('Sorry, I did not catch that as something I can do. Say help to hear what I can do.'); return; }
    say('I did not understand. Try: summarise my day, daily tasks, custom tasks, reminders, add task follow up with Raj tomorrow, or remind me in 10 minutes to call Priya. Unlock Voice commands to open pages and add, update or delete records.');
  }

  /* ---------- voice input: "live mic" ----------
   * Click a mic button once and it STAYS ON (it re-listens by itself) until you:
   *   - click the mic again (manual turn-off) or say "stop listening",
   *   - stop talking to it for MIC_IDLE_MS (unused timer), or
   *   - log off / close the tab.
   * While it is on it only reacts to real commands; other talk in the room is ignored. */
  const MIC_IDLE_MS = 5 * 60 * 1000;            // turns itself off after 5 minutes without a spoken command
  const micBtn = $('.va-mic'), cmdInput = $('.va-input');
  const liveBtn = document.getElementById('vaLiveMic');   // separate mic button in the top bar
  const micState = $('.va-mic-state');
  const SS = {
    get() { try { return JSON.parse(sessionStorage.getItem('va-live') || 'null'); } catch (e) { return null; } },
    set(v) { try { sessionStorage.setItem('va-live', JSON.stringify(v)); } catch (e) {} },
    clear() { try { sessionStorage.removeItem('va-live'); } catch (e) {} }
  };
  const micMessage = code => {
    switch (code) {
      case 'not-allowed': case 'service-not-allowed':
        return window.isSecureContext
          ? 'The microphone is blocked. Click the lock / camera icon in the address bar, set Microphone to Allow, reload the page and try again.'
          : 'Browsers only allow the microphone on secure pages. Open the ATS with https:// (or http://localhost on this PC) and try again.';
      case 'audio-capture': return 'No microphone was found. Connect or enable a microphone and try again.';
      case 'network': return 'Browser voice input needs an internet connection (Chrome and Edge send the audio to their speech service). Type a command or use the buttons; spoken replies still work offline.';
      case 'language-not-supported': return 'Your browser language is not supported for voice input.';
      default: return 'Microphone error: ' + code;
    }
  };
  async function checkMicAccess() {   // asks for permission up front, so a block shows a clear reason instead of a bare error code
    if (!window.isSecureContext) return micMessage('not-allowed');
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return '';
    try { const st = await navigator.mediaDevices.getUserMedia({ audio: true }); st.getTracks().forEach(t => t.stop()); return ''; }
    catch (e) {
      if (e && (e.name === 'NotFoundError' || e.name === 'DevicesNotFoundError' || e.name === 'OverconstrainedError')) return micMessage('audio-capture');
      if (e && (e.name === 'NotReadableError' || e.name === 'TrackStartError')) return 'The microphone is being used by another app. Close it and try again.';
      return micMessage('not-allowed');
    }
  }

  let live = false, rec = null, micBusy = false, lastHeard = 0, quickEnds = 0, restartTimer = 0, lang = 'en-US', muted = false, lastReply = '';
  function paintMic() {
    [micBtn, liveBtn].forEach(b => { if (!b) return; b.classList.toggle('listening', live); b.setAttribute('aria-pressed', live ? 'true' : 'false'); });
    if (liveBtn) liveBtn.title = live ? 'Mic is ON. Click to turn it off. (It also turns off after 5 minutes unused, or when you log off.)' : 'Turn the mic on to talk to the app';
    if (micState) micState.textContent = live ? 'Mic ON · stays on until you turn it off, 5 min unused, or log off' : '';
    cmdInput.placeholder = live ? 'Listening… talk to me' : 'Type or say a command…';
    paintCard();
  }
  function stopLive(msg) {
    live = false; muted = false; clearTimeout(restartTimer); SS.clear();
    const r = rec; rec = null;
    if (r) { r.onend = null; r.onerror = null; r.onresult = null; try { r.abort(); } catch (e) {} }
    paintMic(); if (msg) addLog(msg);
  }
  function heard() { lastHeard = Date.now(); SS.set({ on: true, last: lastHeard }); }
  const speakingNow = () => !!(synth && (synth.speaking || synth.pending));

  function listen() {
    if (!live || muted) return;
    const r = new Rec(); rec = r;
    r.lang = lang; r.interimResults = true; r.continuous = true; r.maxAlternatives = 1;
    const began = Date.now();
    r.onresult = e => {
      if (muted || speakingNow()) return;              // do not react to the assistant's own voice
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) { cmdInput.value = ''; const q = t.trim(); if (q) handle(q, { live: true }); }
        else interim += t;
      }
      cmdInput.value = interim; if (interim) { heard(); cardSet('you', interim + '…'); cardState('Hearing you…'); }
    };
    r.onerror = e => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;           // normal while waiting; onend restarts
      if (e.error === 'language-not-supported' && lang !== 'en-US') { lang = 'en-US'; return; }
      if (e.error === 'network') return;                                      // onend retries; repeated failures stop below
      stopLive(micMessage(e.error));                                          // blocked / no mic: turn off and say why
    };
    r.onend = () => {
      if (rec === r) rec = null;
      if (!live || muted) return;
      quickEnds = (Date.now() - began < 1500) ? quickEnds + 1 : 0;           // engine dying instantly again and again
      if (quickEnds >= 6) { stopLive('The microphone keeps stopping (check your internet connection and microphone). Click the mic to try again.'); return; }
      restartTimer = setTimeout(listen, quickEnds ? 800 : 250);
    };
    try { r.start(); }
    catch (err) { if (!/already started/i.test(err && err.message || '')) restartTimer = setTimeout(listen, 800); }
  }
  async function startLive(silentResume) {
    if (!Rec) { addLog('Voice input is not supported in this browser. Use Chrome, Edge or Safari, or type a command / use the buttons.'); return; }
    if (live || micBusy) return;
    micBusy = true;
    const problem = silentResume ? '' : await checkMicAccess();
    micBusy = false;
    if (problem) { addLog(problem); return; }
    stop();                                            // stop any speech that is playing
    live = true; quickEnds = 0; lang = navigator.language || 'en-US';
    heard(); paintMic(); listen();
    if (!silentResume) addLog('Mic is ON. Say a command, e.g. “summarise my day” or “open candidates”. Click the mic again or say “stop listening” to turn it off.');
  }
  function toggleMic() { if (live) stopLive('Mic turned off.'); else startLive(false); }

  /* two-way conversation: stop listening while the assistant talks (so it never hears itself), listen again when it finishes */
  turn.start = () => {
    if (!live) return;
    muted = true; clearTimeout(restartTimer);
    const r = rec; rec = null;
    if (r) { r.onend = null; r.onerror = null; r.onresult = null; try { r.abort(); } catch (e) {} }
    cmdInput.value = ''; paintCard();
  };
  turn.end = () => { if (!live || !muted) return; muted = false; quickEnds = 0; heard(); paintCard(); restartTimer = setTimeout(listen, 350); };
  setInterval(() => { if (live && muted && synth && !synth.speaking && !synth.pending) turn.end(); }, 700);   // safety: some browsers never fire "speech ended"

  /* live card: shows what the mic heard and what the assistant answered, even when the panel is closed */
  let card = null;
  function ensureCard() {
    if (card) return card;
    card = document.createElement('div'); card.className = 'va-live-card'; card.hidden = true;
    card.innerHTML = '<div class="vlc-head"><span class="vlc-dot"></span><b class="vlc-state">Listening…</b><button type="button" class="vlc-off">Turn mic off</button></div>' +
      '<div class="vlc-line vlc-you"></div><div class="vlc-line vlc-bot"></div>';
    document.body.appendChild(card);
    card.querySelector('.vlc-off').addEventListener('click', () => stopLive('Mic turned off.'));
    return card;
  }
  const cardSet = (who, t) => { const c = ensureCard(), el = c.querySelector(who === 'you' ? '.vlc-you' : '.vlc-bot'); el.textContent = (who === 'you' ? 'You: ' : 'Assistant: ') + t; };
  const cardState = t => { ensureCard().querySelector('.vlc-state').textContent = t; };
  function paintCard() {
    const c = ensureCard(); c.hidden = !live;
    if (!live) { c.querySelector('.vlc-you').textContent = ''; c.querySelector('.vlc-bot').textContent = ''; return; }
    c.dataset.mode = muted ? 'speaking' : 'listening';
    cardState(muted ? 'Assistant is speaking…' : 'Listening… talk to me');
  }
  cardHook.fn = (t, me) => { if (!live) return; if (me) { cardSet('you', t); cardState('Thinking…'); } else { cardSet('bot', t); lastReply = t; } };

  /* small talk and general questions: answered out loud, no password needed */
  const firstName = () => ((document.querySelector('.rt-user-text b')?.textContent || '').trim().split(/\s+/)[0] || '');
  function converse(q) {
    const s = q.toLowerCase().replace(/[.!?,]+/g, ' ').replace(/\s+/g, ' ').trim(), n = firstName();
    if (/^(?:hi|hello|hey|hey there|hi there|hello there|good (?:morning|afternoon|evening))(?: assistant)?$/.test(s)) return 'Hello' + (n ? ' ' + n : '') + '! How can I help you?';
    if (/^(?:how are you|how are you doing|how's it going|how do you do)$/.test(s)) return 'I am doing well, thank you. How can I help you?';
    if (/^(?:thanks|thank you|thanks a lot|thank you very much|ok thanks|okay thanks|great thanks)$/.test(s)) return 'You are welcome.';
    if (/^(?:are you there|can you hear me|do you hear me|hello are you there)$/.test(s)) return 'Yes, I can hear you. Go ahead.';
    if (/^(?:who are you|what is your name|what's your name|what are you)$/.test(s)) return 'I am your recruiting assistant. I can read your day, set reminders and tasks, and open pages or update records when voice commands are unlocked.';
    if (/^(?:help|what can you do|what can i say|what can i ask|how do i use this|commands)$/.test(s)) return 'You can talk in your own words, for example: move Priya to shortlisted and book her a call tomorrow at 11. Or say: summarise my day, daily tasks, remind me in 10 minutes to call Priya, add task follow up with Raj tomorrow, or list reminders. After unlocking voice commands you can also say open candidates, find Priya, or schedule interview for Priya tomorrow at 11 am. Say stop listening when you are done.';
    if (/^(?:what(?:'s| is)? the time|what time is it|time please|tell me the time|current time)$/.test(s)) return 'It is ' + new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + '.';
    if (/^(?:what(?:'s| is)? the date|what(?:'s| is) today(?:'s date)?|what day is it|today's date|what is today)$/.test(s)) return 'Today is ' + new Date().toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }) + '.';
    if (/^(?:repeat|say that again|repeat that|come again|pardon|sorry what)$/.test(s)) return lastReply || 'I have not said anything yet.';
    return null;
  }

  setInterval(() => { if (live && Date.now() - lastHeard > MIC_IDLE_MS) stopLive('Mic turned off because it was not used for 5 minutes. Click the mic to turn it on again.'); }, 5000);
  // log off (or session end) => mic off
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('a[href*="logout"]')) { stopLive(); } }, true);
  window.addEventListener('pagehide', () => { const r = rec; if (r) { r.onend = null; try { r.abort(); } catch (e) {} } });   // page change: the next page resumes the mic

  /* ---------- reminder scheduler ---------- */
  function fired() { const f = LS.get('fired', {}); if (f.day !== todayKey()) return { day: todayKey(), keys: {} }; return f; }
  function markFired(key) { const f = fired(); f.keys[key] = 1; LS.set('fired', f); }
  function tick() {
    if (!LS.get('remindersOn', true)) return;
    const now = Date.now(), f = fired();
    if (cache && cache.day === todayKey()) {   // automatic alerts: interviews (15 min before), due emails and tasks
      (cache.reminders || []).forEach(r => {
        if (f.keys[r.key]) return;
        if (r.at) { const t = new Date(r.at).getTime(), start = t - (r.lead || 0) * 6e4; if (now >= start && now <= t + 30 * 6e4) { markFired(r.key); remind(r.text); } }
        else { markFired(r.key); remind(r.text); }
      });
    }
    const done = firedRems(), due = rems.filter(r => r.at <= now && !done[r.id]);   // the reminders you set
    if (due.length) {
      due.forEach(r => { done[r.id] = 1; });
      LS.set('firedRem', done);
      due.forEach(r => { remind(r.text); api('/api/voice-assistant/reminders/' + r.id + '/done', {}).catch(() => {}); });
      rems = rems.filter(r => !done[r.id]); saveRemsCache(); renderRems();
    }
  }
  async function refresh() { const j = await getBriefing('all'); await loadRems(); return j; }

  /* ---------- UI wiring ---------- */
  btn.addEventListener('click', () => { panel.hidden = !panel.hidden; });
  document.addEventListener('click', e => { if (!root.contains(e.target)) panel.hidden = true; });
  root.querySelectorAll('[data-va]').forEach(b => b.addEventListener('click', () => { const k = b.dataset.va; if (k === 'stop') stop(); else brief(k); }));
  $('.va-form').addEventListener('submit', e => { e.preventDefault(); const v = cmdInput.value; cmdInput.value = ''; handle(v); });
  micBtn.addEventListener('click', toggleMic);
  liveBtn?.addEventListener('click', e => { e.stopPropagation(); toggleMic(); });
  $('.va-voice')?.addEventListener('change', e => LS.set('voice', e.target.value));
  const bind = (sel, key, def) => { const el = $(sel); el.checked = LS.get(key, def); el.addEventListener('change', () => { LS.set(key, el.checked); if (key === 'remindersOn' && el.checked && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission(); }); };
  bind('.va-auto', 'autoBrief', false); bind('.va-rem', 'remindersOn', true); bind('.va-remspeak', 'speakReminders', true);
  if (!synth) { const b = $('.va-badge'); b.textContent = 'text only'; b.classList.add('off'); }

  const remForm = $('.va-rem-form'), remText = $('.va-rem-text'), remAt = $('.va-rem-at');
  if (remAt) { const soon = new Date(Date.now() + 3600e3); soon.setSeconds(0, 0); remAt.value = toLocalInput(soon.getTime()); remAt.min = toLocalInput(Date.now()); }
  remForm?.addEventListener('submit', async e => {
    e.preventDefault();
    const text = remText.value.trim(), ms = remAt.value ? new Date(remAt.value).getTime() : NaN;
    if (!text) { addLog('Type what I should remind you about.'); remText.focus(); return; }
    if (isNaN(ms)) { addLog('Pick a date and time for the reminder.'); return; }
    const res = await addRem(ms, text);
    if (res.ok) { remText.value = ''; addLog('Reminder set for ' + fmt(res.at) + ': ' + text); if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); }
    else addLog(res.error);
  });
  renderRems();

  $('.va-svc-unlock').addEventListener('submit', async e => {
    e.preventDefault(); const f = $('.va-svc-pw'), pw = f.value; f.value = ''; svcErr('');
    if (!pw) return;
    const j = await svcPost('/api/voice-service/unlock', { password: pw });
    if (j.ok) { svc.unlocked = true; svc.blocked = 0; bumpDeadline(); renderSvc(); addLog('Voice commands unlocked.'); }
    else { if (j.needs_setup) svc.hasPw = false; svcErr(j.error || 'Could not unlock.'); renderSvc(); }
  });
  ['.va-svc-new', '.va-svc-new2'].forEach(sel => $(sel).addEventListener('input', checkRules));
  $('.va-svc-setup').addEventListener('submit', async e => {
    e.preventDefault(); svcErr('');
    const login = $('.va-svc-login').value, a = $('.va-svc-new').value, b = $('.va-svc-new2').value;
    if (!login) { svcErr('Enter your login password to confirm it is you.'); return; }
    if (!checkRules()) { svcErr('The voice password does not meet every rule yet.'); return; }
    ['.va-svc-login', '.va-svc-new', '.va-svc-new2'].forEach(s2 => { $(s2).value = ''; }); checkRules();
    const j = await svcPost('/api/voice-service/setup', { login_password: login, new_password: a, confirm_password: b });
    if (j.ok) { svc.hasPw = true; svc.showSetup = false; svc.unlocked = true; bumpDeadline(); renderSvc(); addLog('Voice password saved. Voice commands are unlocked.'); }
    else svcErr(j.error || 'Could not save the voice password.');
  });
  $('.va-svc-change').addEventListener('click', () => { svc.showSetup = true; svcErr(''); renderSvc(); });
  $('.va-svc-cancel').addEventListener('click', () => { svc.showSetup = false; svcErr(''); renderSvc(); });
  $('.va-svc-lock').addEventListener('click', () => lockNow());
  let hiddenAt = 0;   // leaving the tab for a few minutes locks voice commands too
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (svc.unlocked && hiddenAt && Date.now() - hiddenAt > 180000) lockNow('Voice commands locked because this tab was hidden for a while.');
  });
  renderSvc(); svcStatus(); setInterval(svcStatus, 30000); setInterval(svcTick, 1000);

  // the mic was on before this page loaded (same tab, not logged off, not idle): switch it back on
  { const prev = SS.get(); if (prev && prev.on && Rec && Date.now() - (prev.last || 0) < MIC_IDLE_MS) { lastHeard = prev.last; startLive(true); } else if (prev) SS.clear(); }
  paintMic();

  refresh().then(async () => {
    await migrateLegacy();
    tick();
    // Optional: read the daily summary once per day, on the first click/keypress (browsers block speech before a gesture).
    if (LS.get('autoBrief', false) && LS.get('briefedDay', '') !== todayKey()) {
      const go = () => { LS.set('briefedDay', todayKey()); brief('all'); };
      document.addEventListener('pointerdown', go, { once: true });
    }
  });
  setInterval(tick, 15000);
  setInterval(refresh, 5 * 60000);
})();

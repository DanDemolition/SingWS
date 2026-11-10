// SingWS 2 ↔ KaraFun's web player (experiment; see karafun.rs). Runs in every
// page the KaraFun window loads. KaraFun's web player is a Vue app whose
// stores (Pinia) hold the search, queue and player: SingWS calls those, the
// way the player's own buttons do, and hears back through `window.ipc`.
(() => {
  if (window.top !== window) return;
  const post = message => { try { window.ipc.postMessage(JSON.stringify(message)); } catch (e) {} };
  const sleep = ms => new Promise(done => setTimeout(done, ms));
  let pinia = null;
  // KaraFun's own playback state (mediaSession.setPlaybackState): 3 playing, others not.
  let playbackState = 0;
  let results = [];

  const app = () => { const el = document.getElementById('app'); return el && el.__vue_app__; };
  const store = name => pinia && pinia._s.get(name);
  // The stores, once the player has started (each page load).
  function connect() {
    const vue = app();
    const found = vue && vue.config.globalProperties.$pinia;
    if (!found || !found._s.get('player') || !found._s.get('queue') || !found._s.get('mediaSession')) return false;
    if (found === pinia) return true;
    pinia = found;
    store('mediaSession').$onAction(({ name, args }) => { if (name === 'setPlaybackState') playbackState = args[0]; });
    post({ type: 'ready' });
    return true;
  }
  // An error's words: its message, or what's in it (KaraFun throws plain objects too).
  const describe = error => { if (error && error.message) return error.message; if (error && typeof error === 'object') { try { return JSON.stringify(error); } catch (e) {} } return String(error); };
  const fail = (what, error) => { if (what === 'Play') blankUntil(null); post({ type: 'error', message: `${what}: ${describe(error)}` }); };
  // The song the audience picture is blank until (null: not blank); see
  // singwsPlay. Cleared by the status check below once it plays.
  let blankFor = null;
  const blankUntil = id => { blankFor = id; document.documentElement.classList.toggle('singws-starting', id !== null); };

  // KaraFun's songs for `term`, as its own search page finds them.
  async function find(term) {
    const router = app().config.globalProperties.$router;
    await router.push({ path: '/search', query: { q: term } });
    let search = null;
    for (let i = 0; i < 80; i++) {
      await sleep(150);
      search = store('search');
      if (search && !search.$state.loading && search.$state.lastQuery === term) break;
    }
    const found = (search && search.$state.result && search.$state.result.songs) || [];
    // Only KaraFun's own catalogue: its community songs (sung by users,
    // with no artist) never started when played this way.
    results = found.filter(item => item.type === 'song' && item.content && item.content.artist && item.content.artist.name);
    if (results.length < found.length) post({ type: 'log', message: `search "${term}": left out ${found.length - results.length} community song(s) (${[...new Set(found.filter(i => !results.includes(i)).map(i => i.type))].join(', ')})` });
    return results;
  }

  // Play song `id` now, cutting off any song that's on. The player plays
  // what its search found, so a song chosen elsewhere (SingWS's library,
  // from KaraFun's song list) is looked up first, by `query` (its artist
  // and title), and picked out by its id.
  // KaraFun's web player numbers songs its own way (NOFX - Linoleum is 58970
  // there, 3241559 in the catalogue SingWS uses), so when the id is not among
  // the results the song is picked by artist and title, and the host keeps
  // hearing the id it asked for.
  let alias = null;
  const norm = t => String(t || '').toLowerCase().normalize('NFKD').replace(/[^\p{L}\p{N}]+/gu, '');
  // The matching rules of SingWS's own KaraFun search (the ones that worked with the KaraFun app): qualifiers such as "(live)", "[Remix]", "(Karaoke
  // Version)" do not count in a title; "&" and "and" are the same; a title match needs its ARTIST confirmed, except when exactly one result has that
  // title (the old "selected KaraFun title without artist confirmation" case).
  const QUAL = /(karaoke|version|explicit|clean|remaster|live|feat\.?|ft\.?|remix|acoustic|radio edit|single|mix)/i;
  const stripQualifiers = t => String(t || '').replace(/\(([^)]*)\)/g, (m, inner) => (QUAL.test(inner) ? ' ' : m)).replace(/\[([^\]]*)\]/g, (m, inner) => (QUAL.test(inner) ? ' ' : m));
  const words = t => String(t || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/&/g, ' and ').replace(/[’'`]/g, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  const titleKey = t => words(stripQualifiers(t)).replace(/\s+/g, '');
  const artistWords = t => words(t).split(' ').filter(w => w.length >= 3 && !['and', 'the', 'feat', 'featuring'].includes(w));
  const artistMatches = (have, want) => {
    const h = words(have).replace(/\s+/g, ''), w = words(want).replace(/\s+/g, '');
    if (!h || !w) return false;
    if (h.includes(w) || w.includes(h)) return true;
    const hw = artistWords(have);
    return artistWords(want).some(x => hw.includes(x));
  };
  const choose = (list, id, wantArtist, wantTitle, query) => {
    const byId = list.find(r => r.content.id === id);
    if (byId) return byId;
    if (wantTitle) {
      const key = titleKey(wantTitle);
      let same = list.filter(r => titleKey(r.content.title) === key);
      // The version whose title is written exactly like the request first, the "(live)" ones after.
      same = same.sort((x, y) => (words(y.content.title) === words(wantTitle)) - (words(x.content.title) === words(wantTitle)));
      const confirmed = same.filter(r => artistMatches(r.content.artist.name, wantArtist));
      if (confirmed.length) return confirmed[0];
      if (same.length === 1) return same[0];
      return null;
    }
    const q = words(query).replace(/\s+/g, '');
    return list.find(r => words(`${r.content.artist.name} ${r.content.title}`).replace(/\s+/g, '') === q)
      || list.find(r => q.includes(words(r.content.artist.name).replace(/\s+/g, '')) && q.includes(titleKey(r.content.title)));
  };
  const askedId = web => (alias && alias.web === web ? alias.asked : web);
  window.singwsPlay = async (id, query, extra) => {
    // Black until this song plays: KaraFun's canvas still shows the last
    // song's lyrics while the new one is found and loaded.
    blankUntil(id);
    if (!connect()) return fail('Play', 'KaraFun is still starting');
    extra = extra || {};
    let item = results.find(r => r.content.id === id);
    // SingWS's own query ladder first (artist + title, title + artist, title, artist: cleaned of qualifiers, "&" as "and"), then the plain request.
    const asked = [query, ...(Array.isArray(extra.queries) ? extra.queries : [])].filter(q => typeof q === 'string' && q.trim());
    if (!item && asked.length) {
      const terms = [];
      for (const q of asked) {
        // As written, then without punctuation, which KaraFun's search can trip on (it found nothing for "Alice in Chains Would?").
        const plain = q.replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
        for (const t of [q, plain]) if (t && !terms.includes(t)) terms.push(t);
      }
      for (const term of terms) {
        try {
          // Right after a sign-in the player is still reloading its session and its search can answer nothing: ask again a few times.
          let list = await find(term);
          for (let again = 0; again < 3 && !list.length; again++) { post({ type: 'log', message: `search "${term}": no answer yet, asking again` }); await sleep(4000); list = await find(term); }
          item = choose(list, id, extra.artist || '', extra.title || '', query || term);
        } catch (error) { return fail('Play', error); }
        if (item) break;
        post({ type: 'log', message: `search "${term}": no match among ${results.length} result(s) [${results.map(r => `${(r.content.artist && r.content.artist.name) || ''} - ${r.content.title}`).join('; ')}]` });
      }
    }
    if (!item && query && !results.length) return fail('Play', `KaraFun's search found nothing for "${query}" (no internet? Settings → KaraFun account… → Reload KaraFun once it's back)`);
    if (!item) return fail('Play', `KaraFun didn't find song ${id}${query ? ` ("${query}")` : ''}`);
    try {
      const web = item.content.id;
      alias = { asked: id, web };
      const queue = store('queue');
      // KaraFun's own queue is kept (across sessions too) and it plays on
      // through it: emptied first, so this song is the only one, and nothing
      // of KaraFun's follows it.
      await clearQueue();
      // Added to the empty queue, which plays it...
      await queue.mainAction([item], 'search');
      const added = queue.$state.rawQueue.find(entry => entry.content && entry.content.id === web);
      await sleep(800);
      // ... or, if KaraFun didn't start it by itself, played now.
      if (added && store('mediaSession').$state.currentSongId !== web) await queue.playNowWithQueueIds([added.id]);
      post({ type: 'log', message: `playing ${item.content.artist && item.content.artist.name} - ${item.content.title} (${id}); KaraFun's queue: ${queue.$state.rawQueue.length}` });
    } catch (error) { fail('Play', error); }
  };
  // Remove everything from KaraFun's queue (by queue id, as its own buttons do).
  async function clearQueue() {
    const queue = store('queue');
    for (const entry of [...queue.$state.rawQueue]) { try { await queue.remove(entry.id); } catch (e) {} }
  }
  // Stop: pause, and empty KaraFun's queue (nothing left to go on to).
  window.singwsStop = async () => {
    if (!connect()) return;
    blankUntil(null);
    try { store('player').pause(); await clearQueue(); } catch (error) { fail('Stop', error); }
  };
  // KaraFun's remote (phones joining by its QR code and short address, shown
  // over its lyrics): off whenever it's on (signed in or not; KaraFun can turn
  // it on again), so only SingWS runs the show and only SingWS's QR code shows.
  let turningOff = false;
  async function turnRemoteOff() {
    turningOff = true;
    try { await store('remote').remoteDisable(); post({ type: 'log', message: 'remote turned off' }); }
    catch (error) { post({ type: 'log', message: `remote not turned off: ${describe(error)}` }); }
    // Tried again in 10 s at most, if it's still on.
    setTimeout(() => { turningOff = false; }, 10000);
  }
  // KaraFun's own Sign In: opens karafun.com/connect?code=… (in the browser:
  // see karafun.rs), and this player is signed in once that's done there.
  // KaraFun's own Sign out (the page reloads signed out; the remote is
  // turned off again at the next sign-in).
  window.singwsSignOut = async () => {
    if (!connect()) return;
    try { await store('auth').logout(); post({ type: 'log', message: 'signed out' }); } catch (error) { fail('Sign out', error); }
  };
  // Sign in with the account's email and password through KaraFun's own login call (the same one its sign-in form makes); no browser needed.
  // Reports only whether it worked and KaraFun's own reason text: never the email or the password.
  window.singwsLogin = async (email, password) => {
    if (!connect()) return fail('Login', 'KaraFun is still starting');
    try {
      await store('auth').login({ login: email, password });
      post({ type: 'log', message: 'login: sent (the next status says whether it signed in)' });
    } catch (error) { post({ type: 'log', message: `login: failed (${describe(error)})` }); }
  };
  // KaraFun's own chart (Discover > Top > "Top US" is id 7): open it, scroll its list to the bottom until every row has loaded (it loads 30 at a
  // time), and report the songs in rank order. Needs no sign-in.
  window.singwsTop = async (id) => {
    if (!connect()) return fail('Top', 'KaraFun is still starting');
    try {
      const router = app().config.globalProperties.$router;
      await router.push({ path: `/discover/tops/${id}` });
      const key = `pl_${id}`;
      const list = () => { const s = store('songs'); return s && s.$state.lists && s.$state.lists[key]; };
      for (let i = 0; i < 60 && !(list() && list().total); i++) await sleep(250);
      if (!list()) return fail('Top', `the chart ${id} did not load`);
      let last = -1, stalled = 0;
      for (let i = 0; i < 120 && list().items.length < list().total && stalled < 12; i++) {
        const scroller = [...document.querySelectorAll('*')].find(e => /(auto|scroll)/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 50);
        if (scroller) scroller.scrollTop = scroller.scrollHeight;
        await sleep(800);
        if (list().items.length === last) stalled++; else { stalled = 0; last = list().items.length; }
      }
      const songs = list().items.filter(item => item.type === 'song' && item.content && item.content.title && item.content.artist && item.content.artist.name)
        .map(item => ({ title: item.content.title, artist: item.content.artist.name, seconds: item.content.len || 0 }));
      post({ type: 'top', id, total: list().total, songs });
    } catch (error) { fail('Top', error); }
  };
  window.singwsSignIn = async () => {
    if (!connect()) return fail('Sign in', 'KaraFun is still starting');
    try { await store('auth').remoteLogin(); } catch (error) { fail('Sign in', error); }
  };
  const player = (what, call) => { if (!connect()) return; try { call(store('player')); } catch (error) { fail(what, error); } };
  // Key: `semitones` from the original. KaraFun shifts the song playing by
  // steps (queue.pitchShift), and keeps where it is in the queue entry's
  // settings: shifted by the difference.
  window.singwsKey = async semitones => {
    if (!connect()) return;
    try {
      const queue = store('queue');
      const entry = queue.$state.rawQueue[0];
      const now = Number(entry && entry.settings && entry.settings.audio && entry.settings.audio.pitch) || 0;
      if (semitones !== now) await queue.pitchShift(semitones - now);
      const set = queue.$state.rawQueue[0];
      post({ type: 'log', message: `key ${now} -> ${semitones} (now ${set && set.settings && set.settings.audio && set.settings.audio.pitch})` });
    } catch (error) { fail('Key', error); }
  };
  // Tempo in percent (100 = original). KaraFun's steps are 1% each
  // (queue.tempoShift; its panel shows a tempo of 1 as "+1 %"), kept like the key.
  window.singwsTempo = async percent => {
    if (!connect()) return;
    try {
      const queue = store('queue');
      const entry = queue.$state.rawQueue[0];
      const now = Number(entry && entry.settings && entry.settings.audio && entry.settings.audio.tempo) || 0;
      const wanted = percent - 100;
      if (wanted !== now) await queue.tempoShift(wanted - now);
      post({ type: 'log', message: `tempo ${100 + now}% -> ${percent}%` });
    } catch (error) { fail('Tempo', error); }
  };
  // Volume, 0-100 (as KaraFun's own volume slider sets it).
  window.singwsVolume = volume => player('Volume', p => p.volumeSet(volume));
  window.singwsPause = () => player('Pause', p => p.pause());
  window.singwsResume = () => player('Resume', p => p.resume());
  // As KaraFun's own seek bar does: seekTo while dragging, then seekEnd on
  // letting go. Without seekEnd the player stayed "mid-drag" and froze.
  // seekTo takes a moment (it returns a promise): seekEnd straight after it
  // undid the seek while playing, so it waits for it, as the bar's events do.
  window.singwsSeek = async seconds => {
    if (!connect()) return;
    const p = store('player');
    const from = p.$state.currentTime;
    try {
      await p.seekTo(seconds);
      await sleep(50);
      await p.seekEnd();
    } catch (error) { return fail('Seek', error); }
    await sleep(700);
    post({ type: 'log', message: `seek from ${from}s to ${seconds}s: now at ${p.$state.currentTime}s` });
  };

  // On the audience screen: KaraFun's lyrics picture (a canvas it draws on)
  // fills the window, over the rest of the page.
  // A clear sheet over it all catches the mouse, so nothing of KaraFun's page
  // is clicked; a double-click asks SingWS to toggle the audience screen's
  // fullscreen (as a double-click on the audience screen does).
  window.singwsAudience = on => {
    let style = document.getElementById('singws-audience');
    let shield = document.getElementById('singws-shield');
    if (on && !style) {
      style = document.createElement('style');
      style.id = 'singws-audience';
      style.textContent = 'canvas[id^="__eg_engine"]{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;z-index:2147483646!important;background:#000!important}html,body{overflow:hidden!important}#singws-shield{position:fixed;inset:0;z-index:2147483647;background:transparent;cursor:default}#singws-shield.still{cursor:none}html.singws-starting #singws-shield{background:#000}#axeptio_overlay{display:none!important}';
      (document.head || document.documentElement).appendChild(style);
    }
    if (on && !shield) {
      shield = document.createElement('div');
      shield.id = 'singws-shield';
      const swallow = event => { event.preventDefault(); event.stopPropagation(); };
      for (const name of ['mousedown', 'mouseup', 'click', 'contextmenu', 'wheel', 'pointerdown', 'pointerup', 'auxclick']) shield.addEventListener(name, swallow, { passive: false });
      shield.addEventListener('dblclick', event => { swallow(event); post({ type: 'fullscreen' }); });
      // A click brings the audience screen to the front (as a click on it does without KaraFun).
      shield.addEventListener('mousedown', () => post({ type: 'raise' }));
      // The cursor shows while the mouse moves, and hides after a second, as on mpv's audience screen.
      let still = null;
      shield.addEventListener('mousemove', () => {
        shield.classList.remove('still');
        clearTimeout(still);
        still = setTimeout(() => shield.classList.add('still'), 1000);
      });
      document.body.appendChild(shield);
    }
    if (!on) { if (style) style.remove(); if (shield) shield.remove(); }
    window.dispatchEvent(new Event('resize'));
  };

  // Where things are, twice a second.
  setInterval(() => {
    if (!connect()) return;
    try {
      const session = store('auth').$state.session || {};
      if (store('remote').$state.session && !turningOff) turnRemoteOff();
      const state = store('player').$state;
      const songId = store('mediaSession').$state.currentSongId;
      if (blankFor !== null && askedId(songId) === blankFor && playbackState === 3) blankUntil(null);
      const entry = store('queue').$state.rawQueue.find(e => e.content && e.content.id === songId);
      post({ type: 'status', signedIn: !!session.userId, plan: session.planCaption || '', queue: store('queue').$state.rawQueue.length,
        playing: playbackState === 3, state: playbackState, position: state.currentTime || 0, duration: state.duration || 0,
        song: entry ? { id: askedId(songId), title: entry.content.title || '', artist: (entry.content.artist && entry.content.artist.name) || '' } : null });
    } catch (error) {}
  }, 500);
})();

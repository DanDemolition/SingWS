<?php
$user = preg_replace('/[^A-Za-z0-9_\-]/', '', $_GET['user'] ?? $_POST['user'] ?? 'default');
$accentColor = '#7C3DFF';
$accentPath = __DIR__ . '/../tenants/' . $user . '/accent.txt';
if (is_file($accentPath)) { $c = trim(file_get_contents($accentPath)); if ($c !== '') $accentColor = $c; }
$requestUri = (string)($_SERVER['REQUEST_URI'] ?? '');
$isDawPage = (bool)preg_match('~/daw-controls(?:\.php|/|$)~', $requestUri);
$pageTitle = $isDawPage ? 'SingWS DAW' : 'SingWS Host Controls';
$dawManifestHref = '/daw-manifest.php?user=' . rawurlencode($user);
?><!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title><?= htmlspecialchars($pageTitle, ENT_QUOTES) ?></title>
<?php if ($isDawPage): ?>
<meta name="application-name" content="SingWS DAW">
<meta name="theme-color" content="<?= htmlspecialchars($accentColor, ENT_QUOTES) ?>">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="SingWS DAW">
<link rel="manifest" href="<?= htmlspecialchars($dawManifestHref, ENT_QUOTES) ?>">
<link rel="apple-touch-icon" href="/daw-assets/daw-icon-180.png">
<link rel="icon" type="image/png" sizes="16x16" href="/daw-assets/daw-favicon-16.png">
<link rel="icon" type="image/png" sizes="32x32" href="/daw-assets/daw-favicon-32.png">
<link rel="icon" type="image/png" sizes="48x48" href="/daw-assets/daw-favicon-48.png">
<link rel="icon" href="/daw-assets/daw-favicon.ico">
<?php endif; ?>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700&family=Geist:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#0a0a0d;
  --bg-elev-1:#131318;
  --bg-elev-2:#1a1a22;
  --bg-elev-3:#22222d;
  --fg:#f5f4f8;
  --fg-secondary:#9a98a4;
  --fg-tertiary:#5d5b67;
  --accent:<?= htmlspecialchars($accentColor, ENT_QUOTES) ?>;
  --accent-glow:color-mix(in oklab,var(--accent),transparent 70%);
  --danger:#ff5f7a;
  --danger-bg:color-mix(in oklab,var(--danger),transparent 85%);
  --ok:#36d68a;
  --line:rgba(255,255,255,0.07);
  --line-strong:rgba(255,255,255,0.12);
  --font-display:'Bricolage Grotesque',system-ui,sans-serif;
  --font-ui:'Geist',ui-sans-serif,system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
html{background:#050507;color-scheme:dark}
body{
  margin:0;
  background:
    radial-gradient(80% 50% at 50% 0%,color-mix(in oklab,var(--accent),transparent 92%) 0%,transparent 70%),
    #050507;
  color:var(--fg);
  font-family:var(--font-ui);
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:520px;margin:0 auto;padding:24px 16px 56px}
h1{font-family:var(--font-display);font-size:26px;font-weight:700;letter-spacing:-0.02em;margin:0 0 2px}
.sub{color:var(--fg-secondary);font-size:14px;margin-bottom:16px}
.card{background:var(--bg-elev-1);border:1px solid var(--line);border-radius:16px;padding:18px;margin:12px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.rotation{display:grid;grid-template-columns:1fr;gap:8px}
.slot{background:var(--bg-elev-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-height:66px}
.slot.current{border-color:var(--accent);background:color-mix(in oklab,var(--accent),var(--bg-elev-2) 90%)}
.slot-label{color:var(--fg-tertiary);font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}
.slot-name{font-family:var(--font-display);font-size:20px;font-weight:700;line-height:1.2;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slot-title{color:var(--fg-secondary);font-size:13px;font-weight:500;line-height:1.2;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wide{grid-column:1/-1}
button,input,select{font:inherit;font-family:var(--font-ui)}
button{
  min-height:52px;
  border:1px solid var(--line-strong);
  border-radius:12px;
  background:var(--bg-elev-2);
  color:var(--fg);
  font-weight:600;
  font-size:15px;
  cursor:pointer;
  transition:background .15s,border-color .15s,transform .1s;
  padding:0 16px;
}
button:hover{background:var(--bg-elev-3);border-color:var(--line-strong)}
button:active{transform:scale(.97)}
button.primary{
  background:var(--accent);
  border-color:var(--accent);
  color:#fff;
  box-shadow:0 8px 24px -6px var(--accent-glow);
}
button.primary:hover{filter:brightness(1.1)}
button.daw-btn{background:color-mix(in oklab,var(--accent),transparent 75%);border-color:color-mix(in oklab,var(--accent),transparent 55%);color:var(--fg)}
.danger{background:var(--danger-bg);border-color:color-mix(in oklab,var(--danger),transparent 50%);color:var(--danger)}
.ghost{background:transparent;border-color:var(--line)}
.ghost:hover{background:var(--bg-elev-2)}
.row{display:flex;gap:10px;align-items:center}
.row>*{flex:1}
input[type="text"],input[type="password"],input[type="range"]{
  width:100%;
  min-height:48px;
  border:1px solid var(--line-strong);
  border-radius:12px;
  background:var(--bg-elev-2);
  color:var(--fg);
  padding:10px 16px;
  font-size:15px;
  outline:none;
  transition:border-color .2s,box-shadow .2s;
}
input[type="text"]:focus,input[type="password"]:focus{
  border-color:color-mix(in oklab,var(--accent),transparent 50%);
  box-shadow:0 0 0 3px color-mix(in oklab,var(--accent),transparent 85%);
}
input[type="range"]{min-height:0;padding:0;border:none;background:transparent;accent-color:var(--accent)}
.status{min-height:22px;color:var(--fg-secondary);font-size:13px;margin-top:10px}
.ok{color:var(--ok)}
.bad{color:var(--danger)}
label{display:block;color:var(--fg-secondary);font-size:13px;font-weight:500;margin:12px 0 6px}
.section-label{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--fg-tertiary);margin-bottom:10px}
.range{width:100%;margin-top:6px}
.remember-row{display:flex;align-items:center;gap:10px;margin-top:14px}
.remember-row input[type="checkbox"]{width:20px;height:20px;min-width:20px;accent-color:var(--accent);cursor:pointer}
.remember-row label{margin:0;cursor:pointer;color:var(--fg);font-size:14px}
.forget-pin{display:none;margin-top:6px;background:none;border:none;color:var(--fg-tertiary);font-size:12px;cursor:pointer;text-decoration:underline;min-height:auto;padding:0}
.forget-pin:hover{color:var(--fg-secondary)}
.pin-saved-note{display:none;color:var(--ok);font-size:12px;margin-top:4px}
@media(min-width:430px){
  .rotation{grid-template-columns:1fr 1.1fr 1fr}
  .slot-name{font-size:18px}
}
@media(max-width:380px){
  .grid{grid-template-columns:1fr}
  button{min-height:48px}
}
</style></head>
<body>
<main class="wrap">
  <h1>SingWS Host Controls</h1>
  <div class="sub">Commands run on the laptop app.</div>

  <section id="login" class="card">
    <label>User</label>
    <input id="user" value="<?= htmlspecialchars($user, ENT_QUOTES) ?>" autocomplete="username">
    <label>Host PIN / Password</label>
    <input id="pin" type="password" autocomplete="current-password">
    <div class="remember-row">
      <input type="checkbox" id="rememberPin">
      <label for="rememberPin">Remember PIN on this device</label>
    </div>
    <div id="pinSavedNote" class="pin-saved-note">PIN saved on this device.</div>
    <button class="forget-pin" id="forgetPinBtn" onclick="forgetPin()">Forget saved PIN</button>
    <button class="primary wide" onclick="login()" style="margin-top:14px">Unlock Controls</button>
    <div id="loginStatus" class="status"></div>
  </section>

  <section id="controls" style="display:none">
    <div class="card">
      <div class="rotation">
        <div class="slot"><div class="slot-label">Last</div><div id="lastSinger" class="slot-name">—</div><div id="lastTitle" class="slot-title"></div></div>
        <div class="slot current"><div class="slot-label">Current</div><div id="currentSinger" class="slot-name">Waiting</div><div id="currentTitle" class="slot-title"></div></div>
        <div class="slot"><div class="slot-label">Next</div><div id="nextSinger" class="slot-name">—</div><div id="nextTitle" class="slot-title"></div></div>
      </div>
    </div>

    <div class="card">
      <div class="section-label">Playback</div>
      <div class="grid">
        <button class="primary" onclick="send('play_pause')">Play / Pause</button>
        <button onclick="send('play_next')">Next</button>
        <button onclick="send('restart')">Restart</button>
        <button class="danger" onclick="confirmStop()">Stop</button>
        <button onclick="send('seek_backward',{seconds:10})">Seek −10s</button>
        <button onclick="send('seek_forward',{seconds:10})">Seek +10s</button>
      </div>
      <div id="state" class="status">Ready</div>
    </div>

    <div class="card">
      <div class="section-label">Key &amp; Tempo</div>
      <div class="grid">
        <button onclick="send('key_down')">Key −</button>
        <button onclick="send('key_up')">Key +</button>
        <button onclick="send('tempo_down')">Tempo −</button>
        <button onclick="send('tempo_up')">Tempo +</button>
      </div>
    </div>

    <div class="card">
      <div class="section-label">Background Music</div>
      <div class="grid">
        <button class="wide" onclick="send('bg_play_pause')">BGM Play / Pause</button>
      </div>
      <label>BGM Volume</label>
      <input class="range" type="range" min="0" max="100" value="80" oninput="debouncedBgVol(this.value)">
    </div>

    <button class="ghost wide" onclick="logout()">Lock</button>
  </section>
</main>

<script>
const PIN_KEY = 'hc_pin:<?= addslashes($user) ?>';
let lastTap=0,lastCommand=0,bgTimer=null,stateTimer=null;
const api='/api/v1/host_commands.php';

function getUser(){ return document.getElementById('user').value.trim()||'default'; }
async function post(data){ const body=new URLSearchParams(data); const r=await fetch(api,{method:'POST',body,credentials:'same-origin'}); return await r.json(); }

// ── Remember PIN ──────────────────────────────────────────────────────────────
(function initRememberPin(){
  const pinInput = document.getElementById('pin');
  const rememberCb = document.getElementById('rememberPin');
  const savedNote = document.getElementById('pinSavedNote');
  const forgetBtn = document.getElementById('forgetPinBtn');
  const stored = localStorage.getItem(PIN_KEY);
  if (stored) {
    pinInput.value = stored;
    rememberCb.checked = true;
    savedNote.style.display = 'block';
    forgetBtn.style.display = 'inline';
  }
})();

function forgetPin(){
  localStorage.removeItem(PIN_KEY);
  document.getElementById('pin').value = '';
  document.getElementById('rememberPin').checked = false;
  document.getElementById('pinSavedNote').style.display = 'none';
  document.getElementById('forgetPinBtn').style.display = 'none';
}

// ── Login ─────────────────────────────────────────────────────────────────────
async function login(){
  const s = document.getElementById('loginStatus');
  const pinVal = document.getElementById('pin').value;
  s.textContent = 'Checking...'; s.className = 'status';
  try {
    const j = await post({action:'login', user:getUser(), pin:pinVal});
    if (j.ok) {
      // Save PIN if "Remember" is checked
      if (document.getElementById('rememberPin').checked) {
        localStorage.setItem(PIN_KEY, pinVal);
        document.getElementById('pinSavedNote').style.display = 'block';
        document.getElementById('forgetPinBtn').style.display = 'inline';
      } else {
        localStorage.removeItem(PIN_KEY);
      }
      document.getElementById('login').style.display = 'none';
      document.getElementById('controls').style.display = 'block';
      s.textContent = '';
      status('Unlocked','ok');
      refreshState();
      stateTimer = setInterval(refreshState, 1200);
    } else {
      s.textContent = 'Invalid PIN';
      s.className = 'status bad';
    }
  } catch(e) {
    s.textContent = 'Login failed';
    s.className = 'status bad';
  }
}

function status(t,cls=''){ const el=document.getElementById('state'); el.textContent=t; el.className='status '+cls; }

async function send(command,args={}){
  const now=Date.now(); if(now-lastTap<550)return; lastTap=now;
  status('Sending '+command+'...');
  try {
    const j = await post({action:'send', user:getUser(), command, args_json:JSON.stringify(args)});
    if(!j.ok){ status(j.error||'Failed','bad'); return; }
    lastCommand = j.command_id; pollStatus(j.command_id, 0);
  } catch(e) { status('Command failed','bad'); }
}

async function pollStatus(id,n){
  setTimeout(async()=>{
    try {
      const q = new URLSearchParams({action:'status', user:getUser(), command_id:id});
      const r = await fetch(api+'?'+q, {credentials:'same-origin'});
      const j = await r.json();
      const c = j.command;
      if (c&&(c.status==='success'||c.status==='failed')) {
        status((c.status==='success'?'OK: ':'Failed: ')+(c.message||''), c.status==='success'?'ok':'bad');
        return;
      }
      if (n<20) pollStatus(id,n+1); else status('Sent, waiting for laptop...');
    } catch(e){ status('Status check failed','bad'); }
  }, 700);
}

function setSlot(prefix,slot,fallback){
  slot = slot||{};
  document.getElementById(prefix+'Singer').textContent = (slot.singer||fallback);
  document.getElementById(prefix+'Title').textContent = (slot.title||'');
}

async function refreshState(){
  try {
    const q = new URLSearchParams({action:'state', user:getUser()});
    const r = await fetch(api+'?'+q, {credentials:'same-origin'});
    const j = await r.json();
    if (!j.ok) return;
    const rot = (j.state&&j.state.rotation)||{};
    if (rot.current&&rot.next&&rot.current.item_id&&rot.current.item_id===rot.next.item_id){ rot.next={}; }
    setSlot('last',rot.last,'—');
    setSlot('current',rot.current,'Waiting');
    setSlot('next',rot.next,'—');
  } catch(e){}
}

function confirmStop(){ if(confirm('Stop the current singer?')) send('stop'); }
function debouncedBgVol(v){ clearTimeout(bgTimer); bgTimer=setTimeout(()=>send('set_bg_volume',{value:Number(v)/100}),250); }
async function logout(){ if(stateTimer)clearInterval(stateTimer); await post({action:'logout',user:getUser()}); location.reload(); }

// Auto-login if PIN was remembered
(function tryAutoLogin(){
  if (localStorage.getItem(PIN_KEY) && document.getElementById('pin').value) {
    // Small delay so the page renders first
    setTimeout(login, 300);
  }
})();
</script>
</body></html>

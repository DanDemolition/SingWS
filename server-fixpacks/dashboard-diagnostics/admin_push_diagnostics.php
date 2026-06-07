<?php
declare(strict_types=1);

require_once __DIR__ . '/config.inc';
require_once __DIR__ . '/api/v1/_common.inc';
session_start();

if (!isset($_SESSION['user_id'])) {
  $next = urlencode($_SERVER['REQUEST_URI'] ?? '/admin_push_diagnostics.php');
  header("Location: /login.php?next=" . $next);
  exit;
}

$tenant = norm_user_id($_GET['user'] ?? $_POST['user'] ?? '');
if ($tenant === '' || $_SESSION['user_id'] !== $tenant) {
  http_response_code(403);
  echo "Access denied.";
  exit;
}

function push_diag_send_test(string $tenant): array {
  $result = ['subscriptions' => 0, 'sent' => 0, 'failed' => 0, 'expired' => 0, 'codes' => []];
  $db = singer_features_db();
  $st = $db->prepare("
    SELECT id, endpoint, p256dh, auth
    FROM singer_push_subscriptions
    WHERE user_id = :u
      AND is_active = 1
    ORDER BY id DESC
    LIMIT 25
  ");
  $st->bindValue(':u', $tenant, SQLITE3_TEXT);
  $rs = $st->execute();
  $dead = [];
  while ($row = $rs->fetchArray(SQLITE3_ASSOC)) {
    $result['subscriptions']++;
    $code = send_web_push($row, [
      'title' => 'SingWS test notification',
      'message' => 'Push diagnostics test succeeded for this show.',
      'url' => '/index.php?user=' . rawurlencode($tenant),
      'badge' => 1,
      'extra' => ['channel' => 'admin_push_diagnostics'],
    ]);
    $result['codes'][] = $code;
    if ($code >= 200 && $code < 300) $result['sent']++;
    else $result['failed']++;
    if ($code === 404 || $code === 410) {
      $result['expired']++;
      $dead[] = (int)$row['id'];
    }
  }
  $st->close();
  foreach ($dead as $id) {
    $up = $db->prepare("UPDATE singer_push_subscriptions SET is_active = 0 WHERE id = :id");
    $up->bindValue(':id', $id, SQLITE3_INTEGER);
    $up->execute();
    $up->close();
  }
  $db->close();
  return $result;
}

$testResult = null;
$testError = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['send_test_push'])) {
  try {
    $testResult = push_diag_send_test($tenant);
  } catch (Throwable $e) {
    $testError = $e->getMessage();
  }
}

$diag = webpush_diagnostics(true);

function yn(bool $value): string {
  return $value ? 'yes' : 'no';
}

function badge(bool $value): string {
  return '<span class="badge ' . ($value ? 'ok' : 'bad') . '">' . yn($value) . '</span>';
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="/venuestyle.css">
  <title>Push Diagnostics - <?php echo htmlspecialchars($tenant, ENT_QUOTES); ?></title>
  <base href="/">
  <style>
    body { padding: 16px; }
    .wrap { max-width: 980px; margin: 0 auto; }
    .top { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: center; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-top: 18px; }
    .card { padding: 18px; }
    table { width: 100%; border-collapse: collapse; }
    td { padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.08); vertical-align: top; }
    td:last-child { text-align: right; }
    .badge { display: inline-block; min-width: 44px; padding: 3px 8px; border-radius: 999px; font-weight: 700; font-size: 12px; text-align: center; }
    .badge.ok { background: rgba(50, 220, 130, 0.16); color: #7cffb1; }
    .badge.bad { background: rgba(255, 76, 110, 0.16); color: #ff8aa0; }
    .muted { opacity: 0.72; font-size: 13px; overflow-wrap: anywhere; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .btn { display: inline-block; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div>
        <h1>Push Diagnostics</h1>
        <div class="muted">User: <?php echo htmlspecialchars($tenant, ENT_QUOTES); ?></div>
      </div>
      <div class="actions">
        <a class="btn" href="/dashboard.php?user=<?php echo rawurlencode($tenant); ?>">Dashboard</a>
        <form method="post">
          <input type="hidden" name="user" value="<?php echo htmlspecialchars($tenant, ENT_QUOTES); ?>">
          <input type="hidden" name="send_test_push" value="1">
          <button class="btn" type="submit">Send Test Notification</button>
        </form>
      </div>
    </div>

    <?php if ($testError !== ''): ?>
      <div class="card" style="margin-top:18px;"><strong>Test failed:</strong> <?php echo htmlspecialchars($testError, ENT_QUOTES); ?></div>
    <?php elseif (is_array($testResult)): ?>
      <div class="card" style="margin-top:18px;">
        Test result: <?php echo (int)$testResult['sent']; ?> sent,
        <?php echo (int)$testResult['failed']; ?> failed,
        <?php echo (int)$testResult['subscriptions']; ?> subscription(s),
        HTTP codes <?php echo htmlspecialchars(json_encode($testResult['codes']), ENT_QUOTES); ?>.
      </div>
    <?php endif; ?>

    <div class="grid">
      <section class="card">
        <h2>Server</h2>
        <table>
          <tr>
            <td>Environment file loaded</td>
            <td>
              <?php if (!empty($diag['environment_file_loaded'])): ?>
                <?php echo badge(true); ?>
              <?php else: ?>
                <span class="muted">optional</span>
              <?php endif; ?>
            </td>
          </tr>
          <tr><td>VAPID public key loaded</td><td><?php echo badge(!empty($diag['vapid_public_key_loaded'])); ?></td></tr>
          <tr><td>VAPID private key loaded</td><td><?php echo badge(!empty($diag['vapid_private_key_loaded'])); ?></td></tr>
          <tr><td>VAPID key pair valid</td><td><?php echo badge(!empty($diag['vapid_key_valid'])); ?></td></tr>
          <tr><td>Data directory writable</td><td><?php echo badge(!empty($diag['data_dir_writable'])); ?></td></tr>
          <tr><td>Active subscriptions</td><td><?php echo (int)($diag['active_subscriptions'] ?? 0); ?></td></tr>
        </table>
        <p class="muted">Config: <?php echo htmlspecialchars((string)$diag['config_path'], ENT_QUOTES); ?></p>
      </section>

      <section class="card">
        <h2>Browser</h2>
        <table id="browserDiag">
          <tr><td>Service worker registered</td><td data-key="serviceWorkerRegistered">checking</td></tr>
          <tr><td>Push manager available</td><td data-key="pushManagerAvailable">checking</td></tr>
          <tr><td>Subscription created</td><td data-key="subscriptionCreated">checking</td></tr>
          <tr><td>Public VAPID key retrievable</td><td data-key="vapidPublicKeyLoaded">checking</td></tr>
          <tr><td>Public key endpoint HTTP</td><td data-key="vapidPublicKeyHttpStatus">checking</td></tr>
        </table>
        <p class="muted" id="browserDiagError"></p>
      </section>

      <section class="card">
        <h2>Raw Server Snapshot</h2>
        <pre><?php echo htmlspecialchars(json_encode($diag, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), ENT_QUOTES); ?></pre>
      </section>
    </div>
  </div>

  <script>
    (async function () {
      const yesNo = (value) => value ? '<span class="badge ok">yes</span>' : '<span class="badge bad">no</span>';
      const set = (key, value) => {
        const cell = document.querySelector(`[data-key="${key}"]`);
        if (!cell) return;
        cell.innerHTML = typeof value === 'boolean' ? yesNo(value) : String(value);
      };
      const diag = {
        serviceWorkerRegistered: false,
        pushManagerAvailable: 'PushManager' in window,
        subscriptionCreated: false,
        vapidPublicKeyLoaded: false,
        vapidPublicKeyHttpStatus: null,
      };
      try {
        if ('serviceWorker' in navigator) {
          const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
          diag.serviceWorkerRegistered = !!reg;
          if (reg && reg.pushManager) {
            diag.subscriptionCreated = !!(await reg.pushManager.getSubscription());
          }
        }
        const res = await fetch('/api/v1/web_push_public_key.php', { cache: 'no-store' });
        diag.vapidPublicKeyHttpStatus = res.status;
        const json = await res.json().catch(() => null);
        diag.vapidPublicKeyLoaded = !!(json && json.ok && json.public_key);
      } catch (err) {
        document.getElementById('browserDiagError').textContent = String(err && err.message ? err.message : err);
      }
      set('serviceWorkerRegistered', diag.serviceWorkerRegistered);
      set('pushManagerAvailable', diag.pushManagerAvailable);
      set('subscriptionCreated', diag.subscriptionCreated);
      set('vapidPublicKeyLoaded', diag.vapidPublicKeyLoaded);
      set('vapidPublicKeyHttpStatus', diag.vapidPublicKeyHttpStatus || 'n/a');
      try { console.table(diag); } catch (e) { console.log(diag); }
    })();
  </script>
</body>
</html>

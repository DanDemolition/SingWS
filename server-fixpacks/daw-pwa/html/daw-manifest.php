<?php
// Dedicated manifest for the SingWS DAW / Live Player home-screen app.

header('Content-Type: application/manifest+json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');

$user = preg_replace('/[^A-Za-z0-9_\-]/', '', (string)($_GET['user'] ?? $_GET['tenant'] ?? 'default'));
if ($user === '') $user = 'default';

$accentColor = '#7C3DFF';
$tenantAccent = __DIR__ . "/tenants/$user/accent.txt";
$rootAccent = __DIR__ . "/accent.txt";
if (is_file($tenantAccent)) {
  $c = trim((string)@file_get_contents($tenantAccent));
  if ($c !== '') $accentColor = $c;
} elseif (is_file($rootAccent)) {
  $c = trim((string)@file_get_contents($rootAccent));
  if ($c !== '') $accentColor = $c;
}

$startUrl = '/daw-controls.php?user=' . rawurlencode($user);

$manifest = [
  'name' => 'SingWS DAW',
  'short_name' => 'DAW',
  'id' => '/daw-controls.php?user=' . rawurlencode($user),
  'start_url' => $startUrl,
  'scope' => '/',
  'display' => 'standalone',
  'theme_color' => $accentColor,
  'background_color' => '#050507',
  'icons' => [
    ['src' => '/daw-assets/daw-icon-180.png', 'sizes' => '180x180', 'type' => 'image/png', 'purpose' => 'any'],
    ['src' => '/daw-assets/daw-icon-192.png', 'sizes' => '192x192', 'type' => 'image/png', 'purpose' => 'any maskable'],
    ['src' => '/daw-assets/daw-icon-512.png', 'sizes' => '512x512', 'type' => 'image/png', 'purpose' => 'any maskable'],
  ],
];

echo json_encode($manifest, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

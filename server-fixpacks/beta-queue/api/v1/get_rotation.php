<?php
declare(strict_types=1);

require_once __DIR__ . '/_common.inc';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('Expires: 0');

$tenant = $_GET['tenant'] ?? $_GET['user'] ?? 'test';
$tenant = preg_replace('/[^A-Za-z0-9_-]/', '', (string)$tenant);
if ($tenant === '') $tenant = 'test';

$dataDir = defined('DATA_DIR') ? rtrim(DATA_DIR, "/\\") : rtrim(__DIR__ . '/../../data', "/\\");
$file = $dataDir . DIRECTORY_SEPARATOR . 'rotation_' . $tenant . '.json';

if (is_file($file) && is_readable($file)) {
  readfile($file);
  exit;
}

echo json_encode([
  'tenant' => $tenant,
  'rotation' => [],
  'ts' => time(),
], JSON_UNESCAPED_SLASHES);

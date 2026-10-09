<?php
declare(strict_types=1);

define('WP_INSTALLING', true);
require __DIR__ . '/bootstrap.php';
require_once ABSPATH . 'wp-admin/includes/upgrade.php';
if (!is_blog_installed()) {
    wp_install('Signal synthetic lab', 'lab_admin', 'lab@example.invalid', false, '', bin2hex(random_bytes(32)));
}
// Exercise exactly the bundled editor and theme; no downloaded plugins.
update_option('active_plugins', []);
$bridge = new SignalLabBridge();
$bridge->install();
echo "Installed synthetic WordPress lab.\n";

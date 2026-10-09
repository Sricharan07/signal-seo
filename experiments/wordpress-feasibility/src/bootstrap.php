<?php
declare(strict_types=1);

if (PHP_SAPI !== 'cli' || getenv('SIGNAL_FEASIBILITY_LAB') !== '1') {
    throw new RuntimeException('LAB_ONLY');
}
require_once '/var/www/html/wp-load.php';
if (wp_get_environment_type() !== 'local' || DB_NAME !== 'signal_lab') {
    throw new RuntimeException('LAB_ONLY');
}
require_once __DIR__ . '/bridge.php';
$wpdb->suppress_errors(true);
if (!defined('WP_INSTALLING')) {
    wp_set_current_user(1);
}
$wpdb->query('SET SESSION innodb_lock_wait_timeout = 10');

<?php
declare(strict_types=1);

require __DIR__ . '/bootstrap.php';
const BEFORE = '<a href="https://example.invalid/broken">Reference</a>';
const AFTER = '<a href="https://example.invalid/repaired">Reference</a>';
const CONTENT = '<!-- wp:paragraph --><p>Keep this text. ' . BEFORE . '</p><!-- /wp:paragraph -->' . "\n[lab_shortcode]";
$bridge = new SignalLabBridge();
$tests = [];
$children = [];

function same(mixed $actual, mixed $expected): void
{
    if ($actual !== $expected) {
        throw new RuntimeException('Assertion failed: ' . json_encode(['actual' => $actual, 'expected' => $expected]));
    }
}
function raises(string $message, callable $operation): void
{
    try {
        $operation();
    } catch (Throwable $error) {
        same($error->getMessage(), $message);
        return;
    }
    throw new RuntimeException("Expected $message");
}
function page(): int
{
    $id = wp_insert_post(wp_slash([
        'post_title' => 'Synthetic fixture ' . bin2hex(random_bytes(4)),
        'post_content' => CONTENT, 'post_type' => 'page', 'post_status' => 'publish',
        'post_author' => 1, 'post_date' => '2026-01-01 12:00:00',
    ]), true);
    if (is_wp_error($id)) {
        throw new RuntimeException('FIXTURE_FAILED');
    }
    return $id;
}
function intent(int $id): array
{
    return ['op_id' => bin2hex(random_bytes(16)), 'post_id' => $id,
        'expected_content' => CONTENT, 'before' => BEFORE, 'after' => AFTER];
}
function scenario(string $name, callable $run): void
{
    global $tests;
    $start = microtime(true);
    try {
        $run();
        $tests[] = ['name' => $name, 'status' => 'PASS', 'seconds' => round(microtime(true) - $start, 3)];
    } catch (Throwable $error) {
        $tests[] = ['name' => $name, 'status' => 'FAIL', 'error' => $error->getMessage()];
    }
}
function worker(array $request): array
{
    global $children;
    $process = proc_open(['php', '/lab/worker.php'], [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($process)) {
        throw new RuntimeException('WORKER_FAILED');
    }
    $child = ['process' => $process, 'pipes' => $pipes];
    $children[] = $child;
    fwrite($pipes[0], json_encode($request, JSON_THROW_ON_ERROR) . "\n");
    same(line($child), ['checkpoint' => 'STARTED']);
    return $child;
}
function line(array $child): array
{
    $read = [$child['pipes'][1]];
    $write = $except = [];
    if (stream_select($read, $write, $except, 15) !== 1) {
        throw new RuntimeException('WORKER_TIMEOUT');
    }
    $line = fgets($child['pipes'][1]);
    if ($line === false) {
        throw new RuntimeException('WORKER_CLOSED: ' . stream_get_contents($child['pipes'][2]));
    }
    return json_decode($line, true, 512, JSON_THROW_ON_ERROR);
}
function resumeWorker(array $child): void
{
    fwrite($child['pipes'][0], "continue\n");
}
function finish(array $child): array
{
    $result = line($child);
    foreach ($child['pipes'] as $pipe) {
        fclose($pipe);
    }
    same(proc_close($child['process']), 0);
    if (isset($result['error'])) {
        throw new RuntimeException($result['error']);
    }
    return $result['result'];
}
function awaitQuery(string $needle): void
{
    global $wpdb;
    $deadline = microtime(true) + 10;
    do {
        foreach ($wpdb->get_results('SHOW FULL PROCESSLIST', ARRAY_A) as $process) {
            if ($process['Info'] !== null && str_contains($process['Info'], $needle)) {
                return;
            }
        }
        usleep(10000);
    } while (microtime(true) < $deadline);
    throw new RuntimeException('EXPECTED_CONCURRENT_QUERY_NOT_OBSERVED');
}

scenario('Exact pinned WordPress and transactional database are running', function () use ($wpdb) {
    global $wp_version;
    same($wp_version, '6.9.1');
    same(str_starts_with($wpdb->get_var('SELECT VERSION()'), '11.4.10-MariaDB'), true);
    same((bool) wp_using_ext_object_cache(), false);
    same(get_option('active_plugins'), []);
});
scenario('Unique content patch preserves blocks, shortcodes and protected fields', function () use ($bridge) {
    $id = page();
    $before = get_post($id, ARRAY_A);
    $receipt = $bridge->apply(intent($id));
    same($bridge->content($id), str_replace(BEFORE, AFTER, CONTENT));
    same($receipt['before_content'], CONTENT);
    foreach (['post_title', 'post_status', 'post_date', 'post_author', 'post_name'] as $field) {
        same(get_post($id, ARRAY_A)[$field], $before[$field]);
    }
});
scenario('Ambiguous, absent and empty patch targets fail closed', function () {
    foreach ([CONTENT . BEFORE, 'nothing here', ''] as $content) {
        raises('PATCH_NOT_UNIQUE', fn() => SignalLabBridge::patch($content, BEFORE, AFTER));
    }
    raises('PATCH_NOT_UNIQUE', fn() => SignalLabBridge::patch(CONTENT, '', AFTER));
    raises('PATCH_NOT_UNIQUE', fn() => SignalLabBridge::patch(CONTENT, BEFORE, BEFORE));
});
scenario('Unknown fields, oversized values and invalid identities are rejected', function () use ($bridge) {
    $valid = intent(page());
    foreach ([['title' => 'not approved'], ['op_id' => '../bad'], ['post_id' => '1'],
        ['before' => str_repeat('x', 262145)], ['metadata' => null]] as $change) {
        raises('INVALID_INTENT', fn() => $bridge->apply(array_merge($valid, $change)));
    }
});
scenario('Draft and missing resources are not silently published or created', function () use ($bridge) {
    $id = page();
    wp_update_post(['ID' => $id, 'post_status' => 'draft']);
    raises('UNSUPPORTED_RESOURCE', fn() => $bridge->apply(intent($id)));
    raises('UNSUPPORTED_RESOURCE', fn() => $bridge->apply(intent(2147483647)));
    same(get_post_status($id), 'draft');
});
scenario('An editor save committed first causes a version conflict', function () use ($bridge) {
    $id = page();
    $child = worker(['mode' => 'editor', 'post_id' => $id, 'append' => ' Human edit.']);
    finish($child);
    raises('VERSION_CONFLICT', fn() => $bridge->apply(intent($id)));
    same($bridge->content($id), CONTENT . ' Human edit.');
});
scenario('Duplicate operation IDs return the original receipt, not a second mutation', function () use ($bridge) {
    $operation = intent(page());
    $receipt = $bridge->apply($operation);
    same($bridge->apply($operation), $receipt);
    $operation['after'] .= ' different';
    raises('OPERATION_ID_REUSED', fn() => $bridge->apply($operation));
});
scenario('Concurrent duplicate workers serialize on the same operation row', function () use ($bridge) {
    $operation = intent(page());
    $first = worker(['mode' => 'bridge', 'intent' => $operation, 'pause_after_lock' => true]);
    same(line($first), ['checkpoint' => 'LOCKED']);
    $second = worker(['mode' => 'bridge', 'intent' => $operation]);
    awaitQuery($operation['op_id']);
    resumeWorker($first);
    same(finish($first), finish($second));
    same($bridge->content($operation['post_id']), str_replace(BEFORE, AFTER, CONTENT));
});
scenario('Response loss after commit is reconciled from a durable receipt', function () use ($bridge) {
    $operation = intent(page());
    $bridge->apply($operation); // The caller deliberately discards the response.
    $receipt = (new SignalLabBridge())->receipt($operation['op_id']);
    same($receipt['after_content'], $bridge->content($operation['post_id']));
    same($bridge->apply($operation), $receipt);
});
scenario('Crash before commit rolls back both content and receipt', function () use ($bridge) {
    $operation = intent(page());
    $child = worker(['mode' => 'bridge', 'intent' => $operation, 'pause_after_write' => true]);
    same(line($child), ['checkpoint' => 'WROTE']);
    same($bridge->content($operation['post_id']), CONTENT);
    proc_terminate($child['process'], 9);
    foreach ($child['pipes'] as $pipe) {
        fclose($pipe);
    }
    proc_close($child['process']);
    same($bridge->receipt($operation['op_id']), null);
    same($bridge->content($operation['post_id']), CONTENT);
    $bridge->apply($operation);
});
scenario('An external hook survives SQL rollback: automatic execution remains blocked', function () use ($bridge) {
    $operation = intent(page());
    $file = tempnam('/tmp', 'signal-hook-');
    $hook = fn() => file_put_contents($file, "external event\n", FILE_APPEND);
    add_action('save_post_page', $hook);
    try {
        raises('INJECTED_FAILURE', fn() => $bridge->apply($operation, function () {
            throw new RuntimeException('INJECTED_FAILURE');
        }));
        same($bridge->content($operation['post_id']), CONTENT);
        same($bridge->receipt($operation['op_id']), null);
        same(file_get_contents($file), "external event\n");
    } finally {
        remove_action('save_post_page', $hook);
        unlink($file);
    }
});
scenario('Plugin changes to protected fields are detected and rolled back', function () use ($bridge, $wpdb) {
    $operation = intent(page());
    $title = get_the_title($operation['post_id']);
    $hook = fn($id) => $wpdb->update($wpdb->posts, ['post_title' => 'Unapproved'], ['ID' => $id]);
    add_action('save_post_page', $hook);
    try {
        raises('PROTECTED_FIELD_CHANGED', fn() => $bridge->apply($operation));
        same(get_the_title($operation['post_id']), $title);
        same($bridge->receipt($operation['op_id']), null);
    } finally {
        remove_action('save_post_page', $hook);
    }
});
scenario('Existing single synthetic metadata row updates atomically with content', function () use ($bridge) {
    $id = page();
    add_post_meta($id, SignalLabBridge::META_KEY, 'Before', true);
    $operation = intent($id);
    $operation['metadata'] = ['before' => 'Before', 'after' => 'After'];
    $bridge->apply($operation);
    same(get_post_meta($id, SignalLabBridge::META_KEY, true), 'After');
    same($bridge->content($id), str_replace(BEFORE, AFTER, CONTENT));
});
scenario('Missing, duplicate and stale metadata are rejected without content changes', function () use ($bridge) {
    foreach (['missing', 'duplicate', 'stale'] as $mode) {
        $id = page();
        if ($mode !== 'missing') {
            add_post_meta($id, SignalLabBridge::META_KEY, 'Stale');
        }
        if ($mode === 'duplicate') {
            add_post_meta($id, SignalLabBridge::META_KEY, 'Duplicate');
        }
        $operation = intent($id);
        $operation['metadata'] = ['before' => 'Before', 'after' => 'After'];
        raises($mode === 'stale' ? 'METADATA_CONFLICT' : 'METADATA_CARDINALITY', fn() => $bridge->apply($operation));
        same($bridge->content($id), CONTENT);
        same($bridge->receipt($operation['op_id']), null);
    }
});
scenario('Metadata and content both roll back on failure before receipt', function () use ($bridge) {
    $id = page();
    add_post_meta($id, SignalLabBridge::META_KEY, 'Before', true);
    $operation = intent($id);
    $operation['metadata'] = ['before' => 'Before', 'after' => 'After'];
    raises('INJECTED_FAILURE', fn() => $bridge->apply($operation, function () {
        throw new RuntimeException('INJECTED_FAILURE');
    }));
    same(get_post_meta($id, SignalLabBridge::META_KEY, true), 'Before');
    same($bridge->content($id), CONTENT);
});
scenario('Nontransactional metadata engines are rejected before mutation', function () use ($bridge, $wpdb) {
    $operation = intent(page());
    try {
        same($wpdb->query("ALTER TABLE {$wpdb->postmeta} ENGINE=MyISAM") !== false, true);
        raises('UNSUPPORTED_ENGINE', fn() => $bridge->apply($operation));
        same($bridge->receipt($operation['op_id']), null);
    } finally {
        same($wpdb->query("ALTER TABLE {$wpdb->postmeta} ENGINE=InnoDB") !== false, true);
    }
});
scenario('Inverse recovery preserves an unrelated human edit', function () use ($bridge) {
    $id = page();
    $receipt = $bridge->apply(intent($id));
    wp_update_post(wp_slash(['ID' => $id, 'post_content' => $receipt['after_content'] . ' Human note.']));
    $recovery = $bridge->recoveryIntent($receipt, $bridge->content($id), 'undo-' . bin2hex(random_bytes(8)));
    $bridge->apply($recovery);
    same($bridge->content($id), CONTENT . ' Human note.');
    same($bridge->receipt($receipt['op_id']), $receipt);
});
scenario('Overlapping edits and changes after recovery preparation block undo', function () use ($bridge) {
    $id = page();
    $receipt = $bridge->apply(intent($id));
    $recovery = $bridge->recoveryIntent($receipt, $bridge->content($id), 'undo-' . bin2hex(random_bytes(8)));
    wp_update_post(wp_slash(['ID' => $id, 'post_content' => str_replace(AFTER, 'Human replacement', $receipt['after_content'])]));
    raises('RECOVERY_CONFLICT', fn() => $bridge->recoveryIntent($receipt, $bridge->content($id), 'undo-conflict'));
    raises('VERSION_CONFLICT', fn() => $bridge->apply($recovery));
    same(str_contains($bridge->content($id), 'Human replacement'), true);
});
scenario('A stale editor can overwrite after Signal commits: not a lifetime lock', function () use ($bridge) {
    $id = page();
    $operation = intent($id);
    $signal = worker(['mode' => 'bridge', 'intent' => $operation, 'pause_after_lock' => true]);
    same(line($signal), ['checkpoint' => 'LOCKED']);
    $editor = worker(['mode' => 'editor', 'post_id' => $id, 'append' => ' Stale editor note.']);
    awaitQuery("UPDATE `wp_posts`");
    resumeWorker($signal);
    finish($signal);
    finish($editor);
    same($bridge->content($id), CONTENT . ' Stale editor note.');
    same($bridge->receipt($operation['op_id'])['after_content'], str_replace(BEFORE, AFTER, CONTENT));
});
scenario('Authoritative receipt alone does not prove current public output', function () use ($bridge) {
    $id = page();
    $bridge->apply(intent($id));
    $response = wp_remote_get('http://wordpress/?page_id=' . $id, ['timeout' => 10]);
    same(is_wp_error($response), false);
    same(wp_remote_retrieve_response_code($response), 200);
    same(str_contains(wp_remote_retrieve_body($response), AFTER), true);
    same(str_contains(wp_remote_retrieve_body($response), BEFORE), false);
    same(get_post($id)->post_content, $bridge->content($id));
});
scenario('The prototype exposes no Signal REST API', function () {
    $response = wp_remote_get('http://wordpress/?rest_route=/signal/v1/operations', ['timeout' => 10]);
    same(wp_remote_retrieve_response_code($response), 404);
});

foreach ($children as $child) {
    if (is_resource($child['process'])) {
        proc_terminate($child['process'], 9);
        foreach ($child['pipes'] as $pipe) {
            if (is_resource($pipe)) {
                fclose($pipe);
            }
        }
        proc_close($child['process']);
    }
}
$failed = count(array_filter($tests, fn($test) => $test['status'] === 'FAIL'));
echo json_encode([
    'schema_version' => 1, 'recorded_at' => gmdate(DATE_ATOM),
    'wordpress' => $wp_version, 'php' => PHP_VERSION, 'database' => $wpdb->get_var('SELECT VERSION()'),
    'theme' => ['name' => wp_get_theme()->get('Name'), 'version' => wp_get_theme()->get('Version')],
    'cache' => 'WordPress per-process object cache; no persistent cache or CDN',
    'production_authority' => false, 'metadata_mapping' => 'synthetic only; Yoast NOT_EXECUTED',
    'passed' => count($tests) - $failed, 'failed' => $failed, 'tests' => $tests,
], JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . "\n";

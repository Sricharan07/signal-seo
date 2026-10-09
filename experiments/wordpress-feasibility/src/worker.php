<?php
declare(strict_types=1);

require __DIR__ . '/bootstrap.php';
$input = json_decode(trim(fgets(STDIN)), true, 512, JSON_THROW_ON_ERROR);
function checkpoint(string $name): void
{
    echo json_encode(['checkpoint' => $name]) . "\n";
    fflush(STDOUT);
    if (trim((string) fgets(STDIN)) !== 'continue') {
        throw new RuntimeException('BARRIER_CLOSED');
    }
}
try {
    echo json_encode(['checkpoint' => 'STARTED']) . "\n";
    fflush(STDOUT);
    if ($input['mode'] === 'editor') {
        $post = get_post($input['post_id']);
        if (!empty($input['pause'])) {
            add_filter('wp_insert_post_data', function ($data) {
                checkpoint('EDITOR_PREPARED');
                return $data;
            });
        }
        $result = wp_update_post(wp_slash([
            'ID' => $post->ID, 'post_content' => $post->post_content . $input['append'],
        ]), true);
        if (is_wp_error($result)) {
            throw new RuntimeException('EDITOR_FAILED');
        }
        $result = ['post_id' => $result];
    } else {
        $afterWrite = !empty($input['pause_after_write']) ? fn() => checkpoint('WROTE') : null;
        $afterLock = !empty($input['pause_after_lock']) ? fn() => checkpoint('LOCKED') : null;
        $result = (new SignalLabBridge())->apply($input['intent'], $afterWrite, $afterLock);
    }
    echo json_encode(['result' => $result], JSON_THROW_ON_ERROR) . "\n";
} catch (Throwable $error) {
    echo json_encode(['error' => $error->getMessage()]) . "\n";
    exit(1);
}

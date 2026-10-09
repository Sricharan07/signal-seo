<?php
declare(strict_types=1);

// Deliberately CLI-only experimental code. This is not a deployable plugin.
if (PHP_SAPI !== 'cli' || getenv('SIGNAL_FEASIBILITY_LAB') !== '1') {
    throw new RuntimeException('LAB_ONLY');
}

final class SignalLabBridge
{
    public const META_KEY = '_signal_lab_description';
    private string $table;

    public function __construct()
    {
        global $wpdb;
        $this->table = $wpdb->prefix . 'signal_lab_operations';
    }

    private function query(string $sql): int
    {
        global $wpdb;
        $result = $wpdb->query($sql);
        if ($result === false) {
            throw new RuntimeException('DATABASE_ERROR');
        }
        return (int) $result;
    }

    public function install(): void
    {
        $this->query("CREATE TABLE IF NOT EXISTS {$this->table} (
            op_id varchar(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
            intent_hash char(64) CHARACTER SET ascii NOT NULL,
            receipt longtext NULL
        ) ENGINE=InnoDB");
    }

    public function receipt(string $id): ?array
    {
        global $wpdb;
        $value = $wpdb->get_var($wpdb->prepare("SELECT receipt FROM {$this->table} WHERE op_id = %s", $id));
        return $value === null ? null : json_decode($value, true, 512, JSON_THROW_ON_ERROR);
    }

    public function content(int $postId): string
    {
        global $wpdb;
        $value = $wpdb->get_var($wpdb->prepare("SELECT post_content FROM {$wpdb->posts} WHERE ID = %d", $postId));
        if ($value === null) {
            throw new RuntimeException('RESOURCE_MISSING');
        }
        return $value;
    }

    public static function patch(string $content, string $before, string $after): string
    {
        if ($before === '' || $before === $after || substr_count($content, $before) !== 1) {
            throw new RuntimeException('PATCH_NOT_UNIQUE');
        }
        return str_replace($before, $after, $content);
    }

    private function checkEngines(): void
    {
        global $wpdb;
        foreach ([$wpdb->posts, $wpdb->postmeta, $this->table] as $table) {
            $engine = $wpdb->get_var($wpdb->prepare(
                'SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', $table
            ));
            if ($engine !== 'InnoDB') {
                throw new RuntimeException('UNSUPPORTED_ENGINE');
            }
        }
    }

    public function apply(array $intent, ?callable $afterWrite = null, ?callable $afterLock = null): array
    {
        global $wpdb;
        $required = ['op_id', 'post_id', 'expected_content', 'before', 'after'];
        $allowed = [...$required, 'metadata'];
        if (array_diff($required, array_keys($intent)) || array_diff(array_keys($intent), $allowed)
            || !is_int($intent['post_id']) || $intent['post_id'] <= 0
            || !is_string($intent['op_id']) || !preg_match('/^[a-zA-Z0-9_-]{1,64}$/D', $intent['op_id'])) {
            throw new RuntimeException('INVALID_INTENT');
        }
        foreach (['expected_content', 'before', 'after'] as $field) {
            if (!is_string($intent[$field]) || strlen($intent[$field]) > 262144) {
                throw new RuntimeException('INVALID_INTENT');
            }
        }
        if (isset($intent['metadata'])) {
            if (!is_array($intent['metadata']) || array_keys($intent['metadata']) !== ['before', 'after']
                || !is_string($intent['metadata']['before']) || !is_string($intent['metadata']['after'])
                || strlen($intent['metadata']['before']) > 2048 || strlen($intent['metadata']['after']) > 2048) {
                throw new RuntimeException('INVALID_INTENT');
            }
        } elseif (array_key_exists('metadata', $intent)) {
            throw new RuntimeException('INVALID_INTENT');
        }
        $this->checkEngines();
        ksort($intent);
        $hash = hash('sha256', json_encode($intent, JSON_THROW_ON_ERROR));
        $postId = $intent['post_id'];
        $this->query('START TRANSACTION');
        try {
            // The unique operation row serializes duplicate delivery across processes.
            $this->query($wpdb->prepare(
                "INSERT INTO {$this->table} (op_id, intent_hash) VALUES (%s, %s) ON DUPLICATE KEY UPDATE op_id = op_id",
                $intent['op_id'], $hash
            ));
            $operation = $wpdb->get_row($wpdb->prepare(
                "SELECT intent_hash, receipt FROM {$this->table} WHERE op_id = %s FOR UPDATE", $intent['op_id']
            ), ARRAY_A);
            if (!$operation || !hash_equals($operation['intent_hash'], $hash)) {
                throw new RuntimeException('OPERATION_ID_REUSED');
            }
            if ($operation['receipt'] !== null) {
                $this->query('COMMIT');
                return json_decode($operation['receipt'], true, 512, JSON_THROW_ON_ERROR);
            }
            $post = $wpdb->get_row($wpdb->prepare("SELECT * FROM {$wpdb->posts} WHERE ID = %d FOR UPDATE", $postId), ARRAY_A);
            if (!$post || $post['post_type'] !== 'page' || $post['post_status'] !== 'publish') {
                throw new RuntimeException('UNSUPPORTED_RESOURCE');
            }
            if ($post['post_content'] !== $intent['expected_content']) {
                throw new RuntimeException('VERSION_CONFLICT');
            }
            if ($afterLock !== null) {
                $afterLock();
            }
            $after = self::patch($post['post_content'], $intent['before'], $intent['after']);
            $meta = null;
            if (isset($intent['metadata'])) {
                $rows = $wpdb->get_results($wpdb->prepare(
                    "SELECT meta_id, meta_value FROM {$wpdb->postmeta} WHERE post_id = %d AND meta_key = %s FOR UPDATE",
                    $postId, self::META_KEY
                ), ARRAY_A);
                if (count($rows) !== 1) {
                    throw new RuntimeException('METADATA_CARDINALITY');
                }
                if ($rows[0]['meta_value'] !== $intent['metadata']['before']) {
                    throw new RuntimeException('METADATA_CONFLICT');
                }
                $meta = $rows[0];
            }
            // wp_update_post merges cached data. Clear it after locking the authoritative row.
            clean_post_cache($postId);
            $result = wp_update_post(wp_slash(['ID' => $postId, 'post_content' => $after]), true);
            if (is_wp_error($result)) {
                throw new RuntimeException('PROVIDER_REJECTED');
            }
            if ($meta !== null) {
                $this->query($wpdb->prepare(
                    "UPDATE {$wpdb->postmeta} SET meta_value = %s WHERE meta_id = %d",
                    $intent['metadata']['after'], $meta['meta_id']
                ));
            }
            if ($afterWrite !== null) {
                $afterWrite();
            }
            $actual = $wpdb->get_row($wpdb->prepare("SELECT * FROM {$wpdb->posts} WHERE ID = %d", $postId), ARRAY_A);
            if ($actual['post_content'] !== $after) {
                throw new RuntimeException('VERIFICATION_FAILED');
            }
            foreach ($post as $field => $value) {
                if (!in_array($field, ['post_content', 'post_modified', 'post_modified_gmt'], true) && $actual[$field] !== $value) {
                    throw new RuntimeException('PROTECTED_FIELD_CHANGED');
                }
            }
            $receipt = [
                'op_id' => $intent['op_id'], 'post_id' => $postId, 'intent_hash' => $hash,
                'before_content' => $post['post_content'], 'after_content' => $after,
                'before' => $intent['before'], 'after' => $intent['after'],
                'metadata' => $intent['metadata'] ?? null,
            ];
            $this->query($wpdb->prepare("UPDATE {$this->table} SET receipt = %s WHERE op_id = %s",
                json_encode($receipt, JSON_THROW_ON_ERROR), $intent['op_id']));
            $this->query('COMMIT');
            clean_post_cache($postId);
            return $receipt;
        } catch (Throwable $error) {
            $this->query('ROLLBACK');
            clean_post_cache($postId);
            throw $error;
        }
    }

    public function recoveryIntent(array $receipt, string $current, string $operationId): array
    {
        if ($receipt['metadata'] !== null || substr_count($current, $receipt['after']) !== 1
            || str_contains($current, $receipt['before'])) {
            throw new RuntimeException('RECOVERY_CONFLICT');
        }
        return [
            'op_id' => $operationId, 'post_id' => $receipt['post_id'], 'expected_content' => $current,
            'before' => $receipt['after'], 'after' => $receipt['before'],
        ];
    }
}

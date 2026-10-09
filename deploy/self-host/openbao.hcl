ui = false
disable_mlock = true
api_addr = "https://openbao:8200"
cluster_addr = "https://openbao:8201"

audit "file" "operator-file" {
  options {
    file_path = "/openbao/logs/audit.jsonl"
    log_raw = "false"
    mode = "0600"
  }
}
storage "raft" {
  path = "/openbao/file"
  node_id = "self-host-1"
}
listener "tcp" {
  address = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_cert_file = "/openbao/tls/server.pem"
  tls_key_file = "/openbao/tls/server-key.pem"
  tls_min_version = "tls12"
  max_request_size = 65536
}

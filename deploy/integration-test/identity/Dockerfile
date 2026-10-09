FROM quay.io/keycloak/keycloak:26.7.3@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54 AS builder
ENV KC_DB=postgres KC_HEALTH_ENABLED=true KC_METRICS_ENABLED=false KC_VAULT=file
ENV KC_HTTP_RELATIVE_PATH=/identity KC_HTTP_MANAGEMENT_RELATIVE_PATH=/management
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.7.3@sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54
COPY --from=builder /opt/keycloak/ /opt/keycloak/
COPY --chmod=0555 start.sh /opt/keycloak/bin/signal-start.sh
USER 1000:1000
ENTRYPOINT ["/opt/keycloak/bin/signal-start.sh"]

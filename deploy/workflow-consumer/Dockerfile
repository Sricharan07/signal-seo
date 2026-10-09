# syntax=docker/dockerfile:1.7
FROM python:3.12.14-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG SIGNAL_RELEASE
RUN python -c "import os,re; value=os.environ.get('SIGNAL_RELEASE',''); assert re.fullmatch(r'sha256:[a-f0-9]{64}', value)" \
    && groupadd --gid 10001 signal \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin signal

LABEL org.opencontainers.image.title="Signal workflow command consumer" \
      org.opencontainers.image.revision="${SIGNAL_RELEASE}" \
      org.opencontainers.image.source="https://github.com/Sricharan07/signal-seo"

ENV HOME=/nonexistent \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/signal \
    SIGNAL_WORKFLOW_CONSUMER_RELEASE=${SIGNAL_RELEASE}

WORKDIR /opt/signal
COPY deploy/workflow-consumer/requirements.txt /tmp/requirements.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir --requirement /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY --chown=10001:10001 services/control_plane/src/signal_core /opt/signal/signal_core
COPY --chown=10001:10001 scripts/run-workflow-consumer.py /opt/signal/run-workflow-consumer.py
COPY --chown=10001:10001 scripts/check-workflow-consumer-health.py /opt/signal/check-workflow-consumer-health.py

USER 10001:10001
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "/opt/signal/check-workflow-consumer-health.py"]
ENTRYPOINT ["python", "/opt/signal/run-workflow-consumer.py"]

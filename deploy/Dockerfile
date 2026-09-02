FROM python:3.12-slim
WORKDIR /app
COPY hew/ ./hew/
RUN useradd -m hew && mkdir -p /var/lib/hew && chown hew /var/lib/hew
USER hew
VOLUME ["/var/lib/hew"]
# Dispatch OFF by default. Phase 1 is log-tier only.
CMD ["python", "-m", "hew.watcher", "--db", "/var/lib/hew/hew.db"]

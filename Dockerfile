# The database is built during the nightly job and copied in here, so the
# running service never touches Xirgo or the tracking platform on the
# request path. A search is then a local SQLite read, which is why answers
# come back in single-digit milliseconds and why an outage at either
# supplier does not take the tool down with it.
FROM python:3.12-slim

WORKDIR /app

# Firebase ID tokens are verified with real signature checking, which needs
# the crypto extra. Without it the service refuses to start rather than
# waving tokens through.
RUN pip install --no-cache-dir requests "pyjwt[crypto]"

COPY canval/ ./canval/
COPY web/ ./web/
COPY canval.db ./canval.db

ENV CANVAL_DB=/app/canval.db \
    PYTHONUNBUFFERED=1

# Cloud Run supplies PORT and terminates TLS in front of us.
CMD exec python -m canval.server --host 0.0.0.0 --port ${PORT:-8080}

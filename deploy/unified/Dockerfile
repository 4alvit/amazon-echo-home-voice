FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
# Debian security updates remain available; record the resulting image digest.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg espeak-ng fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY .github/requirements-build.txt /app/requirements-build.txt
COPY deploy/unified/requirements.lock /app/requirements.lock
RUN pip install --require-hashes --only-binary=:all: -r requirements-build.txt \
    && pip install --require-hashes --no-build-isolation -r requirements.lock
COPY deploy/unified/source-pin.json deploy/unified/fetch_source.py /opt/unified/
RUN python /opt/unified/fetch_source.py /tmp/cast-source \
    && pip install --no-deps --no-build-isolation /tmp/cast-source/google-home-voice-stats-* \
    && mkdir -p /usr/share/licenses/igw-google-voice \
    && cp /tmp/cast-source/google-home-voice-stats-*/LICENSE /usr/share/licenses/igw-google-voice/ \
    && rm -rf /tmp/cast-source
COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
RUN pip install --no-deps --no-build-isolation . \
    && pip check \
    && python -c 'from ask_sdk_webservice_support.verifier import RequestVerifier; RequestVerifier(); import igw_google_voice.cast_service' \
    && groupadd --gid 10001 energyvoice \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin energyvoice
COPY deploy/unified/supervisor.py deploy/unified/healthcheck.py /opt/unified/
ARG ALEXA_REVISION=unknown
LABEL org.opencontainers.image.title="Unified personal Alexa and Cast gateway" \
      org.opencontainers.image.revision=$ALEXA_REVISION \
      org.igw.cast.revision="9b5d8bc87fe8ca776255bb5a11b55521d3d8221f"
USER 10001:10001
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=15s --start-period=100s --retries=3 \
    CMD ["python", "/opt/unified/healthcheck.py"]
CMD ["python", "/opt/unified/supervisor.py"]

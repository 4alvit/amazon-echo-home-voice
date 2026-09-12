FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements-webhook.lock ./
RUN pip install --requirement requirements-webhook.lock \
    && python -c 'from ask_sdk_webservice_support.verifier import RequestVerifier; RequestVerifier()' \
    && groupadd --gid 10001 energyvoice \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin energyvoice
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps .
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--config", "python:amazon_echo_home_voice.gunicorn_config", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "--timeout", "10", "--worker-tmp-dir", "/tmp", "amazon_echo_home_voice.webhook:application"]

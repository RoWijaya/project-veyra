FROM python:3.12-slim@sha256:191083cdf6c7bda8c76158983163ead5d5cf7b77cf3033e805bb0f5296f51b4b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=false
WORKDIR /app
COPY requirements-release.txt ./
RUN python -m pip install --no-cache-dir --only-binary=:all: --no-deps -r requirements-release.txt
COPY pyproject.toml README.md ./
COPY config ./config
COPY core ./core
COPY templates ./templates
COPY manage.py ./
RUN python -m pip install --no-cache-dir --no-build-isolation --no-deps . && python -m pip check
RUN DJANGO_SECRET_KEY=build-only-placeholder-key-over-40-characters-required python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn config.asgi:application --host 0.0.0.0 --port \"${PORT:-8000}\" --proxy-headers --forwarded-allow-ips \"${VEYRA_TRUSTED_PROXY_IPS:-127.0.0.1,::1}\" --no-access-log --log-level warning"]

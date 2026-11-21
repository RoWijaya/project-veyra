FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=false
WORKDIR /app
COPY pyproject.toml README.md ./
COPY config ./config
COPY core ./core
COPY templates ./templates
COPY manage.py ./
RUN pip install --no-cache-dir .
RUN DJANGO_SECRET_KEY=build-only-placeholder-key-over-40-characters-required python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

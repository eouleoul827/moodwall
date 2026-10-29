FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/backend
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
COPY . .
RUN useradd --create-home --uid 10001 moodwall
USER moodwall
EXPOSE 8000
CMD ["python", "-m", "app"]

FROM runtime AS test
USER root
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER moodwall
CMD ["python", "-m", "pytest", "-q"]

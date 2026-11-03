FROM python:3.12-slim AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/backend
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock
RUN useradd --create-home --uid 10001 moodwall

FROM dependencies AS runtime
COPY . .
USER moodwall
EXPOSE 8000
CMD ["python", "-m", "app"]

FROM dependencies AS student-model-dependencies
USER root
COPY requirements-model.lock /tmp/requirements-model.lock
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements-model.lock
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false

FROM student-model-dependencies AS student-model
COPY . .
USER moodwall
CMD ["python", "-m", "app.worker"]

FROM student-model AS student-model-test
USER root
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER moodwall
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM runtime AS test
USER root
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir -r /tmp/requirements-dev.lock
USER moodwall
CMD ["python", "-m", "pytest", "-q"]

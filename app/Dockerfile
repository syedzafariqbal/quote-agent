# Worker image: Python + WeasyPrint system libraries.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HOME=/tmp

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfontconfig1 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-worker.txt .
RUN pip install -r requirements-worker.txt

COPY src/ ./
RUN useradd --uid 10001 --no-create-home appuser
USER 10001

CMD ["python", "-m", "worker.main"]

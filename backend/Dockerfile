FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && pip install ".[production]"

COPY . .
RUN addgroup --system hydrobridge \
    && adduser --system --ingroup hydrobridge hydrobridge \
    && chown -R hydrobridge:hydrobridge /app

USER hydrobridge

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

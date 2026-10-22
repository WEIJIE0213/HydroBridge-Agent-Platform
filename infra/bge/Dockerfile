FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 90 --retries 5 torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 90 --retries 5 transformers==4.51.3 fastapi==0.119.0 uvicorn==0.38.0 sentencepiece==0.2.1
COPY server.py ./
EXPOSE 8080
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# Hugging Face Spaces (Docker SDK) or Render. HF expects port 7860.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/app/.cache FASTEMBED_CACHE_PATH=/app/.cache/fastembed
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
# download the embedding model at build time so cold starts are fast
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"
COPY rankforge ./rankforge
COPY api ./api
RUN useradd -m -u 1000 user && chown -R user /app
USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-7860} --workers 1 --timeout-keep-alive 75"]

FROM semantica-knowledge-explorer:latest

USER root

RUN pip install --no-cache-dir \
    "openai>=1.0.0" \
    "instructor>=1.15.3" \
    "ollama>=0.1.0" \
    "cryptography>=44.0.0" \
    "pdfplumber>=0.11.0"

COPY --chown=semantica:semantica document_extraction_module /app/document_extraction_module

RUN mkdir -p /data/jobs && chown -R semantica:semantica /data

ENV PYTHONUNBUFFERED=1 \
    DOCUMENT_EXTRACT_JOB_ROOT=/data/jobs \
    DOCUMENT_EXTRACT_SETTINGS_DB=/data/settings.db

USER semantica

EXPOSE 9004

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:9004/api/health',timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["python", "-m", "uvicorn", "document_extraction_module.src.app:app", "--host", "0.0.0.0", "--port", "9004"]

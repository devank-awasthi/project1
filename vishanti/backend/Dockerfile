FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models
WORKDIR /app

COPY requirements.txt requirements-ml.txt ./
RUN pip install -r requirements.txt

# WITH_ML=1 installs the local models and downloads them at build time, so the
# running containers never call out to the internet with complaint text.
ARG WITH_ML=1
RUN if [ "$WITH_ML" = "1" ]; then \
      pip install -r requirements-ml.txt && \
      python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); \
from transformers import pipeline; pipeline('sentiment-analysis', model='distilbert-base-uncased-finetuned-sst-2-english')"; \
    fi

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home vishanti && chown -R vishanti /app /models 2>/dev/null || true
USER vishanti
EXPOSE 8000
# --no-access-log: uvicorn's access log would record client IPs (see app/main.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log", "--proxy-headers"]

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN useradd --system --uid 1000 --create-home app && mkdir /data && chown app /data
COPY app ./app
USER app
# DB and backups live in /data (mount a volume there)
ENV DB_PATH=/data/leads.db BACKUP_DIR=/data/backups
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/login',timeout=4).status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

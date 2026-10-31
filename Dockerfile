FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data /app/app/static/uploads \
    && chown -R appuser:appgroup /data /app/app/static/uploads

USER appuser

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-", "app.app:app"]

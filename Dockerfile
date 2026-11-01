# Kundenportal Zahlenwerk: Image für den KVM-2 (wird erst nach Freigabe gebaut und gestartet)
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core poppler-utils \
    && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 --home /app portal
WORKDIR /app
COPY pyproject.toml README.md manage.py ./
COPY auswertung ./auswertung
COPY portal ./portal
COPY kundenportal ./kundenportal
RUN pip install --no-cache-dir ".[portal,pdf,betrieb]" \
    && PORTAL_SECRET_KEY=nur-fuer-den-build PORTAL_MASTER_KEY=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= \
       python manage.py collectstatic --noinput -v0
USER portal
ENV DJANGO_SETTINGS_MODULE=portal.settings PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["gunicorn", "portal.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--max-requests", "1000"]

# Serveur web MIKI (FastAPI) : tableau de bord DIHAL, vue écoutants, API.
# Sans état : tout est dans PostgreSQL et Valkey (DATABASE_URL, VALKEY_URL fournies par Aiven Runtime).
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8080

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY static ./static

EXPOSE 8080
CMD ["python", "serveur.py"]

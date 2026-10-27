# Uma imagem para API e worker; o comando define o papel.
# Fixe o digest da imagem base ao publicar (docker buildx imagetools inspect python:3.13-slim).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin stp
WORKDIR /srv/stp
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY migrations/ migrations/
COPY db/ db/
COPY alembic.ini .

USER 10001:10001
# Padrão: API. O worker usa: python -m app.worker_cli
CMD ["python", "-m", "uvicorn", "app.api:criar_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

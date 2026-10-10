FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Bake alembic so the image is self-contained for external operators who pull it
# and run it without the managed host mounts. The managed compose still mounts
# alembic.ini + alembic/ from the host (read-only), which overrides these copies,
# so managed behaviour is unchanged.
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic

RUN mkdir -p /app/data

EXPOSE 8010

# Run migrations, then serve. Managed deploy overrides this `command:` with its
# own equivalent, so this default only affects standalone/external usage.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8010"]


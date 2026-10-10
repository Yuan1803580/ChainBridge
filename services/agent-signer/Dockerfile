FROM python:3.13-slim
WORKDIR /app
COPY services/agent-signer/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY services/agent-signer .
# Entrypoint auto-generates secrets when absent and optionally runs migrations,
# then execs the command. Backward compatible: the managed compose overrides
# CMD with its own `alembic upgrade head && uvicorn ...` and supplies the
# secrets via environment, so this changes nothing for the managed deploy.
RUN chmod +x ./docker-entrypoint.sh
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8020"]

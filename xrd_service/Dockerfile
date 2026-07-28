FROM registry.dp.tech/public/python:3.13-slim

WORKDIR /app

COPY xrd_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY xrd_service /app/xrd_service/

EXPOSE 8010

CMD ["/app/.venv/bin/python", "-m", "uvicorn", "xrd_service.service:app", "--host", "0.0.0.0", "--port", "8010"]
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY targets/target-agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY targets/target-agent/ ./
EXPOSE 8201
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8201"]

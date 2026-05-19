FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pip setuptools wheel

COPY run_task.py /sandbox/run_task.py

ENTRYPOINT ["python", "/sandbox/run_task.py"]

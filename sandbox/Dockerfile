FROM python:3.11-slim

RUN useradd -m -s /bin/bash sandbox

RUN pip install --no-cache-dir requests

USER sandbox

WORKDIR /sandbox

COPY run_task.py /sandbox/run_task.py

ENTRYPOINT ["python", "/sandbox/run_task.py"]

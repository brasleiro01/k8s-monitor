FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py .

ENV PYTHONUNBUFFERED=1
ENV POSTMORTEM_DIR=/postmortems

RUN mkdir -p /postmortems

CMD ["python", "monitor.py"]

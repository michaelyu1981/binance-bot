FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY app ./app
COPY README.md ./

RUN mkdir -p logs

CMD ["python3", "-m", "app.main"]

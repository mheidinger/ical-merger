# syntax=docker/dockerfile:1

FROM python:3.15-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

FROM python:3.15-slim
RUN useradd --system --uid 1000 --home /app --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app.py .
USER app
EXPOSE 3000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]

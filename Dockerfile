FROM python:3.12-slim

# tzdata so the midnight reset of "Energy today" follows the TZ environment variable
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --system --uid 1000 goodwe

EXPOSE 502 8080
USER goodwe
CMD ["goodwe-modbus"]

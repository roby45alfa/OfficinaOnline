# File: Dockerfile
# Docker configuration for the officina project.

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Expose flask port
EXPOSE 5000

ENV FLASK_ENV=production

# Entrypoint
ENTRYPOINT ["./entrypoint.sh"]

# Convenience build template, not an executed/reproducible image build.
# Replace the base tag with an approved digest after your image/dependency audit.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 dataforge && useradd --uid 10001 --gid 10001 --no-create-home dataforge
WORKDIR /opt/dataforge-team
COPY requirements.txt requirements-s3.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip && python -m pip install --no-cache-dir -r requirements-s3.txt && python -m pip check
COPY app ./app
COPY web ./web
COPY studio ./studio
RUN mkdir -p /var/lib/dataforge /var/backups/dataforge && chown -R 10001:10001 /var/lib/dataforge /var/backups/dataforge
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.12-slim

WORKDIR /srv
# rasterio wheels need libexpat, which the slim image lacks
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# pytest/owslib are dev-only but harmless; keeps one requirements file
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app app
COPY pywps.cfg .
# Public image: the pre-clipped cache (build with scripts/build_cache.py; git-ignored) is baked
# in at /srv/data. For local use mount full scenes over it: -v ./data:/srv/data:ro
COPY cache /srv/data
ENV BHOONIDHI_DATA_DIR=/srv/data
EXPOSE 5000
# single worker + threads: PyWPS async uses multiprocessing and file-based status in outputs/
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "--timeout", "300", "app.server:flask_app"]

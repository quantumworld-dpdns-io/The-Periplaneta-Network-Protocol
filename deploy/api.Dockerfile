# The model server. Build context is the repository root, because the image needs
# the blattella package, the fitted parameters in ml/artifacts and the renewal
# code in netsim that the neural readout still uses.
FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY blattella/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY blattella/ ./blattella/
COPY netsim/renewal.py ./netsim/renewal.py
COPY netsim/__init__.py ./netsim/__init__.py
COPY ml/artifacts/twin_params.json ./ml/artifacts/twin_params.json

RUN adduser --system --group app && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["uvicorn", "blattella.api:app", "--host", "0.0.0.0", "--port", "8000"]

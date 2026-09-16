FROM python:3.12-slim

WORKDIR /app

# deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# correction core + API (all the .py modules)
COPY *.py ./
# If you build the SPA, drop its dist/ here so FastAPI serves it at "/":
#   COPY static/ ./static/

EXPOSE 8000

# --forwarded-allow-ips is overridden by docker-compose with the real proxy IP.
# It must name the proxy's address, never "*".
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

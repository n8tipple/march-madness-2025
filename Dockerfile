FROM python:3.11-slim
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/instance && chown -R app:app /app
USER app
ENV HOST=0.0.0.0 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=3)" || exit 1
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-"]

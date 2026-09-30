FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --create-home appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/data /app/outputs && chown appuser:appuser /app/data /app/outputs
USER appuser
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "from urllib.request import urlopen; urlopen('http://localhost:8501/_stcore/health', timeout=3)"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]

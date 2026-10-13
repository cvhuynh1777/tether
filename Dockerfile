# One container runs both services:
#   - FastAPI backend on 8000 (internal only)
#   - Streamlit frontend on 8501 (the public port)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BACKEND_URL=http://127.0.0.1:8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/
COPY .streamlit/ .streamlit/
COPY start.sh .
RUN chmod +x start.sh

# Run as a non-root user
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

EXPOSE 8501
CMD ["./start.sh"]

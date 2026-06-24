FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
RUN mkdir -p model_store

ENV ML_MODEL_STORE=model_store \
    ML_MIN_WINDOWS=30 \
    ML_CONTAMINATION=0.05

EXPOSE 3050
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3050"]

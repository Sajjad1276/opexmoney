FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway still has a legacy dashboard pre-deploy command that invokes
# "python3 -c ..." with invalid syntax. Route that invocation through the
# repair script, while transparently passing every normal Python invocation
# through to the real interpreter.
RUN cp "$(command -v python3)" /usr/local/bin/python3.real \
    && chmod +x /app/scripts/railway_python3_wrapper.sh \
    && mv /app/scripts/railway_python3_wrapper.sh /usr/local/bin/python3 \
    && chmod +x /usr/local/bin/python3

CMD ["python", "main.py"]

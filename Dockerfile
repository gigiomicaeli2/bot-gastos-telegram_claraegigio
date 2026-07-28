FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD sh -c "mkdir -p site_falso && cd site_falso && python -m http.server $PORT & cd /app && python bot.py"

FROM python:3.9-buster

WORKDIR /app

COPY ./requirements.txt .

RUN set -eux \
    && pip install --no-cache-dir -U -r requirements.txt

COPY . .

CMD ["python", "educom_bot/bot.py"]

import html
import json
import logging
import os
import re
import traceback
from time import time

import mechanicalsoup
import requests
import urllib3
from dotenv import load_dotenv
from telegram import ParseMode
from telegram.ext import CommandHandler, Defaults, Updater

logger = logging.getLogger(__name__)
logging.getLogger("urllib3").setLevel(logging.WARNING)
urllib3.disable_warnings()

load_dotenv()

LK_USERNAME = os.getenv("LK_USERNAME")
LK_PASSWORD = os.getenv("LK_PASSWORD")

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERS_CHAT_ID = os.getenv("BOT_USERS_CHAT_ID").split(",")
BOT_ADMIN_CHAT_ID = os.getenv("BOT_ADMIN_CHAT_ID").split(",")

BOT_REQUEST_KWARGS = ""
if os.getenv("PROXY_URL"):
    BOT_REQUEST_KWARGS = {
        "proxy_url": os.getenv("PROXY_URL"),
    }

URL_BASE = "http://lk.educom.ru/"
URL_LOGIN = "login.html"
URL_NEWS = "news.html"

ENTRY_FILE = "entry.json"
COOKIE_FILE = "cookies.json"

COOKIE_LIFETIME = 30 * 60  # 30m
LK_CHECK_INTERVAL = 30  # 30s

pattern = re.compile(r"\s+")

START_TEXT = """
Ваш <b>chat_id = {}</b>. Для получения уведомлений передайте его администратору бота.

<b>❗️ВНИМАНИЕ❗️</b>
Бот находится в разработке и предоставляется как есть. \
Бот ни при каких условиях не может являться основным источником уведомлений! \
Администратор не несёт ответственности за скорость, качество и точность уведомлений, \
а также за не своевременно просмотренные сообщения в личном кабинете!
"""

MSG_TEXT = """
❗️Обновление в <a href="http://lk.educom.ru/news.html">ЛК Директора</a>❗️
<b>{}</b> {}
💾 <a href="{}">Посмотреть/скачать документ(ы)</a>
"""


def start(update, context):
    """
    Start command handler.
    :param update: The chat update object.
    :param context: The telegram CallbackContext class object.
    :return:
    """
    chat_id = update.effective_chat.id
    text = START_TEXT.format(chat_id)
    context.bot.send_message(chat_id=chat_id, text=text)


def notify_users(context, content):
    """
    Send notification to list of users.
    :param context: The telegram CallbackContext class object.
    :param content: Notification content.
    """
    text = MSG_TEXT.format(
        content["entry_date"], content["entry_title"], content["entry_doc"]
    )
    logger.info("Sending notifications...")
    for chat_id in BOT_USERS_CHAT_ID:
        context.bot.send_message(
            chat_id=chat_id, text=text, disable_web_page_preview=True
        )


def error_handler(update, context):
    """
    Log errors which occur.
    :param update: The telegram Update class object which caused the error.
    :param context: The telegram CallbackContext class object.
    """
    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
    tb_string = "".join(tb_list)

    if update:
        logger.error(msg="Exception while handling an update:", exc_info=context.error)
        text = (
            f"An exception was raised while handling an update\n"
            f"<pre>update = {html.escape(json.dumps(update.to_dict(), indent=2, ensure_ascii=False))}"
            "</pre>\n\n"
            f"<pre>context.chat_data = {html.escape(str(context.chat_data))}</pre>\n\n"
            f"<pre>context.user_data = {html.escape(str(context.user_data))}</pre>\n\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )
    else:
        logger.error(msg="Exception was raised:", exc_info=context.error)
        text = f"An exception was raised\n\n" f"<pre>{html.escape(tb_string)}</pre>"
    for chat_id in BOT_ADMIN_CHAT_ID:
        context.bot.send_message(chat_id=chat_id, text=text)


def refresh_session(forced=False):
    """
    Refresh auth cookie.
    :param forced: force refresh.
    """
    if not os.path.isfile(COOKIE_FILE):
        open(COOKIE_FILE, "w").close()

    file_refresh = time() - COOKIE_LIFETIME
    file_mtime = os.path.getmtime(COOKIE_FILE)
    file_size = os.path.getsize(COOKIE_FILE)

    if forced or file_mtime < file_refresh or file_size == 0:
        browser = mechanicalsoup.StatefulBrowser()
        browser.open(URL_BASE + URL_LOGIN)
        browser.select_form("form")
        browser["username"] = LK_USERNAME
        browser["password"] = LK_PASSWORD
        browser.submit_selected()

        with open(COOKIE_FILE, "w") as f:
            json.dump(requests.utils.dict_from_cookiejar(browser.session.cookies), f)
        logger.debug("Session was refreshed")
        browser.close()
    else:
        logger.debug("Session is OK")


def check_for_updates(context):
    """
    Check website for a new post.
    :param context: The telegram CallbackContext class object.
    """
    refresh_session()
    browser = mechanicalsoup.StatefulBrowser()
    with open(COOKIE_FILE) as f:
        browser.session.cookies.update(json.load(f))

    page = browser.open(URL_BASE + URL_NEWS)
    try:
        assert page.soup.select("div.logout-button")
    except AssertionError:
        refresh_session(True)
        page = browser.open(URL_BASE + URL_NEWS)

    entry = page.soup.select("div.ui.form")[0]
    entry_id = entry.attrs.get("data-element")
    entry_title_full = entry.select("div.title.alf-click-acctitle")[0]
    entry_date = entry_title_full.select("div.ui.label")[0].text.strip()
    entry_title = entry_title_full.text.strip()
    entry_title = re.sub(pattern, " ", entry_title)
    entry_title = entry_title.replace(entry_date, "").strip()
    entry_doc = entry.select("a.item.alf-file-show")[0].attrs.get("href")

    res = {
        "entry_id": html.escape(str(entry_id)),
        "entry_date": html.escape(str(entry_date)),
        "entry_title": html.escape(str(entry_title)),
        "entry_doc": html.escape(str(entry_doc)),
    }

    browser.close()

    # 1st run
    if not os.path.isfile(ENTRY_FILE):
        with open(ENTRY_FILE, "w") as f:
            json.dump(res, f)
            notify_users(context, res)
            logger.debug(res)

    with open(ENTRY_FILE, "r+") as f:
        last_sent_entry = json.load(f)
        if res != last_sent_entry:
            f.seek(0)
            json.dump(res, f)
            f.truncate()
            notify_users(context, res)
            logger.debug(res)


def main():
    defaults = Defaults(parse_mode=ParseMode.HTML)

    if BOT_REQUEST_KWARGS:
        updater = Updater(
            token=BOT_TOKEN, defaults=defaults, request_kwargs=BOT_REQUEST_KWARGS
        )
    else:
        updater = Updater(token=BOT_TOKEN, defaults=defaults)

    dispatcher = updater.dispatcher
    dispatcher.add_error_handler(error_handler)

    dispatcher.add_handler(CommandHandler("start", start))

    j = updater.job_queue
    j.run_repeating(check_for_updates, interval=LK_CHECK_INTERVAL, first=0)

    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    __version__ = "0.0.1"
    logging.basicConfig(
        format="%(asctime)s  [%(name)s:%(lineno)s]  %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    main()

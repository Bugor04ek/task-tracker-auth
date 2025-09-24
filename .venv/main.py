import os
from github import Github
from dotenv import load_dotenv
import telebot
from telebot import types
import requests

# Загружаем .env
load_dotenv()

GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
REPO_NAME = os.environ.get('REPO_NAME')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN не найден! Проверь .env файл.")

# Инициализация
bot = telebot.TeleBot(TELEGRAM_TOKEN)
gh = Github(GITHUB_TOKEN)
repo = gh.get_repo(REPO_NAME)

# Состояния
waiting_for_description = {}
waiting_for_close = {}


# ===== Команды =====

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "Привет! Я бот для управления задачами.\n"
        "Команды: /start, /add_task <описание>, /list_tasks, /close_task"
    )


OAUTH_SERVER = os.environ.get('OAUTH_SERVER_BASE_URL')  # например http://127.0.0.1:5000
SERVICE_SECRET = os.environ.get('OAUTH_SERVICE_SECRET')


def ask_oauth_link(telegram_id):
    resp = requests.post(
        f"{OAUTH_SERVER}/create_state",
        headers={"X-SERVICE-SECRET": SERVICE_SECRET, "Content-Type":"application/json"},
        json={"telegram_id": telegram_id},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()["auth_url"]


def check_authorized(telegram_id):
    resp = requests.get(
        f"{OAUTH_SERVER}/is_authorized",
        headers={"X-SERVICE-SECRET": SERVICE_SECRET},
        params={"telegram_id": telegram_id},
        timeout=10
    )
    resp.raise_for_status()
    j = resp.json()
    return j.get("authorized", False), j.get("github_login")


@bot.message_handler(commands=['login'])
def login_cmd(message):
    try:
        auth_url = ask_oauth_link(message.from_user.id)
        bot.reply_to(message, f"Нажми на ссылку, чтобы авторизоваться в GitHub:\n{auth_url}")
    except Exception as e:
        bot.reply_to(message, f"Ошибка при создании ссылки: {e}")


@bot.message_handler(commands=['add_task'])
def add_task(message):
    ok, gh_login = check_authorized(message.from_user.id)
    if not ok:
        bot.reply_to(message, "⚠️ Сначала авторизуйтесь через /login, чтобы получить доступ.")
        return

    try:
        task_desc = message.text.replace('/add_task', '').strip()
        user_id = message.from_user.id

        if not task_desc:
            waiting_for_description[user_id] = True
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("Отмена", callback_data="cancel"))
            bot.reply_to(message, "Пожалуйста, введи описание задачи.", reply_markup=markup)
            return

        issue = repo.create_issue(title=task_desc, body="Создано через Telegram-бота")
        bot.reply_to(message, f"Задача добавлена: #{issue.number} - {issue.title}")
        print(f"Создан Issue #{issue.number} in {repo.full_name}")

    except Exception as e:
        bot.reply_to(message, f"Ошибка при создании задачи: {str(e)}")


@bot.message_handler(commands=['list_tasks'])
def list_tasks(message):
    ok, gh_login = check_authorized(message.from_user.id)
    if not ok:
        bot.reply_to(message, "⚠️ Сначала авторизуйтесь через /login, чтобы получить доступ.")
        return

    try:
        print(f"/list_tasks от user_id={message.from_user.id}")

        open_issues = list(repo.get_issues(state="open"))

        if not open_issues:
            bot.reply_to(message, "Нет открытых задач!")
            return

        response = "Открытые задачи:\n"
        for issue in open_issues:
            response += f"#{issue.number}: {issue.title}\n"
        bot.reply_to(message, response)

    except Exception as e:
        bot.reply_to(message, f"Ошибка при получении списка задач: {str(e)}")


@bot.message_handler(commands=['close_task'])
def close_task(message):
    ok, gh_login = check_authorized(message.from_user.id)
    if not ok:
        bot.reply_to(message, "⚠️ Сначала авторизуйтесь через /login, чтобы получить доступ.")
        return

    try:
        user_id = message.from_user.id
        open_issues = list(repo.get_issues(state='open'))

        if not open_issues:
            bot.reply_to(message, "Нет открытых задач для закрытия!")
            return

        response = "Выбери номер задачи для закрытия:\n"
        for issue in open_issues:
            response += f"#{issue.number}: {issue.title}\n"
        response += "Введите номер задачи: "

        waiting_for_close[user_id] = True
        bot.reply_to(message, response)

    except Exception as e:
        bot.reply_to(message, f"Ошибка: {str(e)}")


# ===== Обработчики текстов и кнопок =====

@bot.message_handler(content_types=['text'])
def handle_description(message):
    try:
        user_id = message.from_user.id

        if user_id in waiting_for_description:
            task_desc = message.text.strip()
            if not task_desc:
                bot.reply_to(message, "Описание не может быть пустым. Попробуй снова.")
                return

            issue = repo.create_issue(title=task_desc, body="Создано через Telegram-бота")
            bot.reply_to(message, f"Задача добавлена: #{issue.number} - {issue.title}")
            del waiting_for_description[user_id]

        elif user_id in waiting_for_close:
            try:
                task_number = int(message.text.strip())
            except ValueError:
                bot.reply_to(message, "Номер задачи должен быть числом! Попробуй снова.")
                return

            issue = repo.get_issue(task_number)
            issue.edit(state='closed')
            bot.reply_to(message, f"Задача #{task_number} закрыта.")
            del waiting_for_close[user_id]

    except Exception as e:
        bot.reply_to(message, f"Ошибка: {str(e)}")


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    try:
        user_id = call.from_user.id
        if user_id in waiting_for_description and call.data == "cancel":
            del waiting_for_description[user_id]
            bot.answer_callback_query(call.id, "Создание задачи отменено.")
            bot.send_message(user_id, "Задача не создана. Используй /add_task для новой попытки.")
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)}")


# ===== Запуск =====

if __name__ == '__main__':
    print(f"Бот запущен для репозитория {repo.full_name}")
    bot.polling(none_stop=True)

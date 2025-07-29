# 🚀 ONBOARDING-БОТ ДЛЯ СТАЖЁРОВ
# Полная версия: регистрация + проверки + рассылка в 9:00 МСК
# Работает на Render 24/7

import os
import logging
import re
from datetime import datetime, date, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    ExtBot,
)
import gspread
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# === Настройки ===
CREDENTIALS_FILE = 'credentials.json'  # будет в Render как Secret File
SHEET_ID = os.getenv("SHEET_ID")       # через переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# === Логирование ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Подключение к Google Таблице ===
def get_sheet():
    try:
        creds = ServiceAccountCredentials.from_service_account_file(
            CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID)
        return sheet.worksheet("Стажеры")
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к таблице: {e}")
        raise

# === Этапы диалога ===
INPUT_NAME, INPUT_PHONE, SELECT_ROLE, INPUT_START_DATE = range(4)
ROLES = ["Менеджер клиентского сервиса"]

# === Проверка ФИО (кириллица, минимум 2 слова) ===
def is_valid_name(text):
    return bool(re.fullmatch(r'[а-яА-ЯёЁ\s]+', text)) and len(text.strip().split()) >= 2

# === Проверка российского телефона ===
def is_valid_phone(text):
    pattern = r'^(\+7|7|8)?[\s\-()]*[9]\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}$'
    return bool(re.match(pattern, text.strip()))

# === Проверка даты (ДД.ММ.ГГГГ или ДД/ММ/ГГГГ) ===
def is_valid_date(text):
    for fmt in ('%d.%m.%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None

# === Извлечение имени из ФИО ===
def extract_first_name(full_name):
    parts = full_name.strip().split()
    if len(parts) >= 2 and re.match(r'[а-яА-ЯёЁ]', parts[1]):
        return parts[1].capitalize()
    return parts[0].capitalize()

# === /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        "Добро пожаловать в программу адаптации стажёров!\n"
        "Давай познакомимся."
    )
    await update.message.reply_text("Введите ваше ФИО (например: Иванов Иван Иванович):")
    return INPUT_NAME

# === Ввод ФИО с проверкой ===
async def input_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fio = update.message.text.strip()
    if not is_valid_name(fio):
        await update.message.reply_text(
            "❌ Ошибка: введите ФИО на кириллице, минимум две части (например: Петров Иван)."
        )
        return INPUT_NAME

    context.user_data['fio'] = fio
    context.user_data['first_name'] = extract_first_name(fio)

    await update.message.reply_text("Введите ваш номер телефона (российский формат):")
    return INPUT_PHONE

# === Ввод телефона с проверкой ===
async def input_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    if not is_valid_phone(phone):
        await update.message.reply_text(
            "❌ Ошибка: введите корректный российский номер.\n"
            "Примеры:\n"
            "+7 912 345-67-89\n"
            "89123456789\n"
            "7 (912) 345 67 89"
        )
        return INPUT_PHONE

    context.user_data['phone'] = phone
    keyboard = [[KeyboardButton(role)] for role in ROLES]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    await update.message.reply_text("Выберите вашу должность:", reply_markup=reply_markup)
    return SELECT_ROLE

# === Выбор должности ===
async def select_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = update.message.text.strip()
    if role not in ROLES:
        await update.message.reply_text("❌ Выберите должность из списка.")
        return SELECT_ROLE

    context.user_data['role'] = role
    first_name = context.user_data['first_name']

    await update.message.reply_text(
        f"Отлично, {first_name}!\n"
        "Теперь введите дату вашего первого рабочего дня в формате ДД.ММ.ГГГГ или ДД/ММ/ГГГГ:"
    )
    return INPUT_START_DATE

# === Ввод даты 1-го дня с проверкой ===
async def input_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_date = update.message.text.strip()
    parsed_date = is_valid_date(raw_date)

    if not parsed_date:
        await update.message.reply_text(
            "❌ Ошибка: введите дату в формате ДД.ММ.ГГГГ или ДД/ММ/ГГГГ (например: 01.08.2025)"
        )
        return INPUT_START_DATE

    if parsed_date < date.today():
        await update.message.reply_text("❌ Дата не может быть в прошлом.")
        return INPUT_START_DATE

    context.user_data['start_date'] = parsed_date.isoformat()
    user = update.effective_user
    fio = context.user_data['fio']
    role = context.user_data['role']
    tg_nickname = f"@{user.username}" if user.username else f"ID{user.id}"
    chat_id = str(user.id)

    try:
        worksheet = get_sheet()
        new_row = [
            chat_id,
            fio,
            role,
            tg_nickname,
            str(date.today()),
            parsed_date.isoformat(),
            "",
        ] + [""] * 15

        worksheet.append_row(new_row)
        first_name = context.user_data['first_name']

        await update.message.reply_text(
            f"✅ Отлично, {first_name}!\n"
            f"Вы зарегистрированы как *{role}*.\n"
            f"Первый рабочий день: *{raw_date}*\n\n"
            f"Скоро Вы получите всю информацию 🌞\n"
            f"Желаем удачи!",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка записи: {e}")
        await update.message.reply_text("❌ Ошибка сохранения. Обратитесь к HR.")

    return ConversationHandler.END

# === Приветственное сообщение первого дня ===
def get_welcome_message(name):
    return (
        f"Привет, {name}! 👋\n\n"
        "От лица СДЭК поздравляю тебя с началом первого дня в нашей компании! 🎉\n"
        "Для того, чтобы всё прошло идеально, необходимо подготовиться, держи чеклист Успешного первого дня 📋\n\n"
        "1. Придумай приветственное сообщение для чата со всеми сотрудниками 👨‍💻, чтобы быть более оригинальным, "
        "советую написать о том, как тебя зовут, какие у тебя 2-3 суперсилы или просто забавный факт 🦸‍♂️ — "
        "это поможет тебе запомниться 😄\n"
        "2. Проверь, что у тебя есть аватарка в вотсапе, это действительно важно, если нет — поставь свою самую крутую фотку, где ты лучше всех! 🥳\n"
        "3. Сегодня тебе будет предоставлен доступ в наши внутренние ресурсы 🚀. Зайди на СДЭК Space 🌐 и обязательно крутани колесо фортуны 🎡, "
        "а затем — поставь фото на аватарку 🙂\n"
        "4. По прибытии на пункт выдачи познакомься со своим наставником 👨‍🏫, он уже предупреждён и ждёт тебя! 😉\n"
        "5. Тебя ждёт экскурсия в закулисье ПВЗ, отнесись внимательно 👀 и постарайся всё запомнить\n"
        "6. Ознакомься с правилами трудового распорядка 🕒\n"
        "7. Ознакомься с нашим основным регламентом 📜, это своего рода Библия СДЭК (ну ооооочень важный документ) 📖\n"
        "8. Главное — будь собой 🙌, не стесняйся задавать вопросы, проявляй инициативу и всё получится! 🏆\n"
        "9. В конце дня напиши мне впечатления по итогам первого дня!\n\n"
        "Успехов! 🙌\n"
        "Я всегда на связи, если будут сложности — пиши! 💬\n\n"
        "P.S. Добро пожаловать в команду! ❤️‍🔥🚀"
    )

# === Функция: проверка, кто начинает сегодня и отправка приветствия ===
def send_welcome_messages():
    logger.info("🔍 Проверка: кто начинает сегодня...")
    try:
        worksheet = get_sheet()
        rows = worksheet.get_all_values()
        today_str = datetime.now(MOSCOW_TZ).date().isoformat()

        for row in rows[1:]:
            if len(row) < 6:
                continue
            chat_id = row[0]
            start_date = row[5]
            fio = row[1]
            if start_date == today_str:
                name = extract_first_name(fio)
                bot = ExtBot(token=TELEGRAM_TOKEN)
                try:
                    bot.send_message(
                        chat_id=int(chat_id),
                        text=get_welcome_message(name),
                        parse_mode='Markdown'
                    )
                    logger.info(f"✅ Приветствие отправлено: {chat_id}")
                except Exception as e:
                    logger.error(f"❌ Не удалось отправить {chat_id}: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка рассылки: {e}")

# === Запуск бота и планировщика ===
def main():
    # Запускаем планировщик
    scheduler = BlockingScheduler(timezone=MOSCOW_TZ)
    scheduler.add_job(send_welcome_messages, CronTrigger(hour=9, minute=0))  # 9:00 МСК
    logger.info("⏰ Планировщик запущен: будет проверять каждый день в 9:00 по МСК")

    # Запускаем Telegram-бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            INPUT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_name)],
            INPUT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_phone)],
            SELECT_ROLE: [MessageHandler(filters.TEXT & filters.Regex(f"^({'|'.join(ROLES)})$"), select_role)],
            INPUT_START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_start_date)],
        },
        fallbacks=[]
    )
    application.add_handler(conv_handler)

    logger.info("🤖 Telegram-бот запущен и работает 24/7")
    # Запускаем бота в фоне
    from threading import Thread
    Thread(target=application.run_polling, daemon=True).start()

    # Запускаем планировщик
    scheduler.start()

if __name__ == "__main__":
    main()
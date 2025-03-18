import os
import logging
import telebot
import requests
import time
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

# Настройка окружения и логгера
load_dotenv()
logging.basicConfig(level=logging.INFO)

class VinChecker:
    def __init__(self):
        # Проверка обязательных переменных окружения
        self.bot = telebot.TeleBot(os.getenv('TELEGRAM_TOKEN'))
        self.vin = os.getenv('VIN_NUMBER')
        self.chat_id = os.getenv('CHAT_ID')
        
        # Валидация переменных (Улучшение 3)
        if not all([self.bot.token, self.vin, self.chat_id]):
            raise ValueError("TELEGRAM_TOKEN, VIN_NUMBER, and CHAT_ID must be set in .env")
        if not self.chat_id.isdigit():
            raise ValueError("CHAT_ID must be a numeric string")
        if len(self.vin) != 17 or not self.vin.isalnum():
            raise ValueError("VIN_NUMBER must be a 17-character alphanumeric string")

        self.last_result = None
        self.check_interval = int(os.getenv('CHECK_INTERVAL', 86400))  # По умолчанию 24 часа
        
        # Регистрация обработчиков команд
        self.bot.message_handler(commands=['start', 'help'])(self.send_welcome)
        self.bot.message_handler(commands=['check'])(self.manual_check)
        self.bot.message_handler(func=lambda message: True)(self.default_message)
        
        # Настройка планировщика
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(self.automatic_check, 'interval', seconds=self.check_interval)
        
    def send_welcome(self, message):
        """Обработчик команд start/help"""
        self.bot.reply_to(message, "🔍 Бот для проверки технических акций Geely по VIN. Доступные команды:\n/check - ручная проверка")

    def default_message(self, message):
        """Обработчик неизвестных сообщений"""
        self.bot.reply_to(message, "Я понимаю только команды /start, /help и /check.")

    def start(self):
        """Запуск бота и периодических проверок с перезапуском при сбоях (Улучшение 2)"""
        self.scheduler.start()
        logging.info("Периодические проверки запущены")
        while True:
            try:
                self.bot.polling(none_stop=True)
            except Exception as e:
                logging.error(f"Ошибка в polling, перезапуск через 5 секунд: {str(e)}")
                time.sleep(5)  # Задержка перед перезапуском

    def automatic_check(self):
        """Автоматическая проверка с обработкой результатов и уведомлением при сбоях (Улучшение 5)"""
        try:
            result = self.fetch_vin_data()
            if result and result != "Доступных акций нет" and result != self.last_result:
                self.last_result = result
                self.bot.send_message(self.chat_id, f"🔔 Обновление статуса:\n{result}")
                logging.info("Отправлено автоматическое уведомление")
        except Exception as e:
            error_msg = f"⚠️ Критическая ошибка в автоматической проверке: {str(e)}"
            logging.error(error_msg)
            try:
                self.bot.send_message(self.chat_id, error_msg)
            except Exception as send_error:
                logging.error(f"Не удалось отправить уведомление об ошибке: {str(send_error)}")

    def manual_check(self, message):
        """Ручная проверка по команде /check"""
        try:
            result = self.fetch_vin_data()
            response = result if result else "⚠️ Не удалось получить данные"
            self.bot.reply_to(message, response)
        except Exception as e:
            self.bot.reply_to(message, "❌ Произошла ошибка. Пожалуйста, попробуйте позже.")
            logging.error(f"Ошибка ручной проверки: {str(e)}")

    def fetch_vin_data(self):
        """Основная логика получения данных по VIN"""
        with requests.Session() as session:
            try:
                # Первичный запрос для получения cookies
                session.get(
                    'https://www.geely-motors.com/for-owners/technical-center/technical-campaigns',
                    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'},
                    timeout=10
                )

                # Отправка POST-запроса
                response = session.post(
                    'https://www.geely-motors.com/local/ajax/technicalcampaigns_redesign.php',
                    data={
                        'vin': self.vin,
                        'sessid': session.cookies.get('PHPSESSID', ''),
                        'ajaxAction': 'checkVin',
                        'componentName': 'geely:technical.campaigns'
                    },
                    headers={
                        'Bx-ajax': 'true',
                        'X-Requested-With': 'XMLHttpRequest',
                        'Referer': 'https://www.geely-motors.com/for-owners/technical-center/technical-campaigns'
                    },
                    timeout=10
                )

                # Обработка ответа (Улучшение 1: рефакторинг в отдельную функцию)
                return self.parse_response(response)

            except requests.RequestException as e:
                logging.error(f"Ошибка сети: {str(e)}")
                return None

    def parse_response(self, response):
        """Парсинг ответа сервера (Улучшение 1)"""
        if response.status_code != 200:
            logging.error(f"Сервер вернул код {response.status_code}")
            return f"Сервер вернул код {response.status_code}"
        
        try:
            data = response.json()
        except ValueError:
            logging.error(f"Ответ не в формате JSON: {response.text}")
            return "Ответ не в формате JSON"

        if data.get('status') == 'success':
            soup = BeautifulSoup(data.get('html', ''), 'html.parser')
            result = soup.find('p', class_='technical-campaigns__vin-search-table-text')
            if result:
                return result.text
            error_message = soup.find('div', class_='error-message')
            return error_message.text if error_message else "Доступных акций нет"
        
        logging.warning(f"Запрос не успешен: {data}")
        return data.get('data', 'Ошибка при обработке запроса')

if __name__ == '__main__':
    checker = VinChecker()
    checker.start()

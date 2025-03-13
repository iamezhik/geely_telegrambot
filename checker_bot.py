import os
import logging
import telebot
import requests
from bs4 import BeautifulSoup
from threading import Timer, Event
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Настройка окружения и логгера
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class VinChecker:
    def __init__(self):
        # Проверка обязательных переменных окружения
        required_env_vars = ['TELEGRAM_TOKEN', 'VIN_NUMBER', 'CHAT_ID']
        for var in required_env_vars:
            if not os.getenv(var):
                raise EnvironmentError(f"Необходимо установить переменную окружения: {var}")

        # Настройка сессии с retry
        self.session = self._configure_session()
        
        # Инициализация бота с увеличенным timeout
        self.bot = telebot.TeleBot(
            os.getenv('TELEGRAM_TOKEN'),
            threaded=True,
            num_threads=2,
            skip_pending=True
        )
        
        self.vin = os.getenv('VIN_NUMBER')
        self.chat_id = os.getenv('CHAT_ID')
        self.last_result = None
        self.check_interval = int(os.getenv('CHECK_INTERVAL', 86400))
        self.stop_event = Event()

        # Регистрация обработчиков команд
        self.bot.message_handler(commands=['start', 'help'])(self.send_welcome)
        self.bot.message_handler(commands=['check'])(self.manual_check)

    def _configure_session(self):
        """Настройка сессии с повторными попытками"""
        session = requests.Session()
        retry = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(['GET', 'POST'])
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)
        return session

    def send_welcome(self, message):
        """Обработчик команд start/help"""
        self.bot.reply_to(message, "🔍 Бот для проверки технических акций Geely по VIN. Доступные команды:\n/check - ручная проверка")

    def start_periodic_check(self):
        """Запуск периодической проверки"""
        self.schedule_check()
        self.start_polling()

    def start_polling(self):
        """Запуск polling с обработкой ошибок"""
        while not self.stop_event.is_set():
            try:
                self.bot.infinity_polling(
                    long_polling_timeout=30,
                    timeout=20,
                    logger_level=logging.INFO,
                    restart_on_change=True
                )
            except Exception as e:
                logging.error(f"Ошибка polling: {str(e)}. Перезапуск через 60 секунд...")
                self.stop_event.wait(60)

    def schedule_check(self):
        """Планировщик периодической проверки"""
        if not self.stop_event.is_set():
            Timer(self.check_interval, self.schedule_check).start()
            self.automatic_check()

    def automatic_check(self):
        """Автоматическая проверка с обработкой результатов"""
        try:
            result = self.fetch_vin_data()
            if result and result != "Доступных акций нет" and result != self.last_result:
                self.last_result = result
                self.bot.send_message(
                    self.chat_id, 
                    f"🔔 Обновление статуса:\n{result}",
                    timeout=30
                )
                logging.info("Отправлено автоматическое уведомление")
        except Exception as e:
            logging.error(f"Ошибка автоматической проверки: {str(e)}")

    def manual_check(self, message):
        """Ручная проверка по команде /check"""
        try:
            result = self.fetch_vin_data()
            response = result if result else "⚠️ Не удалось получить данные"
            self.bot.reply_to(
                message, 
                response,
                timeout=30
            )
        except Exception as e:
            self.bot.reply_to(message, f"❌ Ошибка: {str(e)}")
            logging.error(f"Ошибка ручной проверки: {str(e)}")

    def fetch_vin_data(self):
        """Основная логика получения данных по VIN"""
        try:
            # Первичный запрос для получения cookies
            self.session.get(
                'https://www.geely-motors.com/for-owners/technical-center/technical-campaigns',
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'},
                timeout=30
            )

            # Отправка POST-запроса
            response = self.session.post(
                'https://www.geely-motors.com/local/ajax/technicalcampaigns_redesign.php',
                data={
                    'vin': self.vin,
                    'sessid': self.session.cookies.get('PHPSESSID', ''),
                    'ajaxAction': 'checkVin',
                    'componentName': 'geely:technical.campaigns'
                },
                headers={
                    'Bx-ajax': 'true',
                    'X-Requested-With': 'XMLHttpRequest',
                    'Referer': 'https://www.geely-motors.com/for-owners/technical-center/technical-campaigns'
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    soup = BeautifulSoup(data.get('html', ''), 'html.parser')
                    result = soup.find('p', class_='technical-campaigns__vin-search-table-text')
                    return result.text if result else "Доступных акций нет"
                return data.get('data', 'Ошибка при обработке запроса')
            return "Сервер не ответил корректно"

        except requests.RequestException as e:
            logging.error(f"Ошибка сети: {str(e)}")
            return None

if __name__ == '__main__':
    try:
        checker = VinChecker()
        checker.start_periodic_check()
    except KeyboardInterrupt:
        checker.stop_event.set()
        logging.info("Бот корректно остановлен")

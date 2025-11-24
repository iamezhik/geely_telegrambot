import os
import logging
import time
from datetime import datetime

import requests
import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class VinChecker:
    def __init__(self):
        # Environment variables validation
        self.token = os.getenv("TELEGRAM_TOKEN")
        self.vin = os.getenv("VIN_NUMBER")
        self.chat_id = os.getenv("CHAT_ID")
        self.check_interval = int(os.getenv("CHECK_INTERVAL", "86400"))
        self.admin_ids = self._parse_admin_ids(os.getenv("ADMIN_IDS", ""))

        if not all([self.token, self.vin, self.chat_id]):
            raise ValueError("TELEGRAM_TOKEN, VIN_NUMBER и CHAT_ID обязательны в .env")

        if not self.chat_id.lstrip("-").isdigit():
            raise ValueError("CHAT_ID должен быть числом")

        if len(self.vin) != 17 or not self.vin.isalnum():
            raise ValueError("VIN_NUMBER должен быть 17-символьной буквенно-цифровой строкой")

        self.bot = telebot.TeleBot(self.token)
        self.last_result = None
        self.last_check_time = None
        self.start_time = datetime.now()
        self.check_counter = 0

        # URLs
        self.base_url = "https://www.geely-motors.com"
        self.campaigns_url = f"{self.base_url}/for-owners/technical-center/technical-campaigns"
        self.ajax_url = f"{self.base_url}/local/ajax/technicalcampaigns_redesign.php"

        # Modern browser headers to bypass WAF
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": self.campaigns_url,
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        # Register handlers
        self.bot.message_handler(commands=["start", "help"])(self.send_welcome)
        self.bot.message_handler(commands=["check"])(self.manual_check)
        self.bot.message_handler(commands=["status"])(self.check_status)
        self.bot.message_handler(commands=["info"])(self.show_info)
        self.bot.message_handler(func=lambda m: True)(self.default_message)

        # Scheduler
        self.scheduler = BackgroundScheduler()
        self.scheduler.add_job(
            self.automatic_check,
            "interval",
            seconds=self.check_interval,
            id="vin_check"
        )

    @staticmethod
    def _parse_admin_ids(admin_str):
        """Parse comma-separated admin user IDs from env"""
        if not admin_str:
            return []
        try:
            return [int(uid.strip()) for uid in admin_str.split(",") if uid.strip()]
        except ValueError:
            logger.warning("Неверный формат ADMIN_IDS в .env, игнорируем")
            return []

    def _is_admin(self, user_id):
        """Check if user is admin (has access to extended commands)"""
        return user_id in self.admin_ids if self.admin_ids else True

    def send_welcome(self, message):
        """Handler for /start and /help commands"""
        text = (
            "🔍 *Бот проверки технических акций Geely*\n\n"
            f"VIN: `{self.vin}`\n"
            f"Интервал: {self.check_interval // 3600}ч\n\n"
            "*Команды:*\n"
            "/check — ручная проверка акций\n"
            "/status — диагностика сайта Geely\n"
            "/info — статистика работы бота\n"
            "/help — это сообщение"
        )
        self.bot.reply_to(message, text, parse_mode="Markdown")

    def default_message(self, message):
        """Handler for unknown messages"""
        self.bot.reply_to(
            message,
            "❓ Неизвестная команда. Используйте /help для списка доступных команд."
        )

    def show_info(self, message):
        """Show bot statistics and runtime info"""
        uptime = datetime.now() - self.start_time
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        
        next_check = "неизвестно"
        job = self.scheduler.get_job("vin_check")
        if job and job.next_run_time:
            next_check = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")

        text = (
            "📊 *Статистика бота*\n\n"
            f"⏱ Аптайм: `{uptime_str}`\n"
            f"🔢 Проверок выполнено: {self.check_counter}\n"
            f"🕒 Последняя проверка: {self.last_check_time or 'еще не было'}\n"
            f"⏭ Следующая проверка: {next_check}\n"
            f"📋 Последний результат:\n{self.last_result or 'нет данных'}"
        )
        self.bot.reply_to(message, text, parse_mode="Markdown")

    def check_status(self, message):
        """Diagnostic command: check Geely website availability"""
        self.bot.send_chat_action(message.chat.id, "typing")
        
        status_lines = ["🔍 *Диагностика сайта Geely*\n"]
        
        # Test 1: Main page availability
        try:
            resp = requests.get(self.campaigns_url, headers=self.headers, timeout=10)
            status_lines.append(
                f"✅ Главная страница: HTTP {resp.status_code} "
                f"({len(resp.content)} bytes)"
            )
            
            # Test 2: Check form presence
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", {"id": "technical-campaigns-form"})
            if form:
                status_lines.append("✅ Форма проверки VIN: найдена")
            else:
                status_lines.append("⚠️ Форма проверки VIN: не найдена (структура изменена?)")
            
        except requests.Timeout:
            status_lines.append("❌ Главная страница: таймаут (>10 сек)")
        except requests.RequestException as e:
            status_lines.append(f"❌ Главная страница: ошибка ({type(e).__name__})")

        # Test 3: AJAX endpoint
        try:
            test_resp = requests.post(
                self.ajax_url,
                data={"ajaxAction": "test"},
                headers=self.headers,
                timeout=10
            )
            status_lines.append(f"✅ AJAX endpoint: HTTP {test_resp.status_code}")
        except requests.Timeout:
            status_lines.append("❌ AJAX endpoint: таймаут")
        except requests.RequestException as e:
            status_lines.append(f"❌ AJAX endpoint: ошибка ({type(e).__name__})")

        # Test 4: DNS resolution
        try:
            import socket
            ip = socket.gethostbyname("www.geely-motors.com")
            status_lines.append(f"✅ DNS: `{ip}`")
        except socket.gaierror:
            status_lines.append("❌ DNS: не резолвится")

        self.bot.reply_to(message, "\n".join(status_lines), parse_mode="Markdown")

    def start(self):
        """Start bot polling and scheduler"""
        logger.info(
            "🚀 Запуск бота (VIN: %s, интервал: %s сек)",
            self.vin,
            self.check_interval
        )
        self.scheduler.start()
        
        # Send startup notification
        try:
            self.bot.send_message(
                self.chat_id,
                f"✅ Бот запущен\nVIN: `{self.vin}`\nПроверка каждые {self.check_interval // 3600}ч",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error("Не удалось отправить уведомление о запуске: %s", e)

        # Polling loop with auto-restart
        while True:
            try:
                self.bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
            except Exception as exc:
                logger.error("Ошибка polling: %s. Перезапуск через 15 сек", exc)
                time.sleep(15)

    def automatic_check(self):
        """Scheduled automatic check"""
        logger.info("⏰ Автоматическая проверка #%s", self.check_counter + 1)
        self.check_counter += 1
        
        result = self.fetch_vin_data()
        self.last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if not result:
            logger.warning("Проверка не удалась, пропускаем уведомление")
            return

        # Send notification only if status changed
        if result != self.last_result:
            self.last_result = result
            
            if "акций нет" in result.lower() or "не найдено" in result.lower():
                msg = f"ℹ️ *Статус обновлен*\nДля VIN `{self.vin}` акции отсутствуют"
            else:
                msg = f"🔔 *Обнаружена акция!*\n\nVIN: `{self.vin}`\n\n{result}"
            
            try:
                self.bot.send_message(self.chat_id, msg, parse_mode="Markdown")
            except Exception as e:
                logger.error("Ошибка отправки уведомления: %s", e)

    def manual_check(self, message):
        """Handler for /check command"""
        logger.info("🖐 Ручная проверка от пользователя %s", message.from_user.id)
        self.bot.send_chat_action(message.chat.id, "typing")
        
        result = self.fetch_vin_data()
        self.last_check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if not result:
            self.bot.reply_to(
                message,
                "⚠️ Не удалось получить данные от сайта Geely. "
                "Попробуйте позже или используйте /status для диагностики."
            )
        else:
            # Format response with emoji based on result
            if "акций нет" in result.lower() or "не найдено" in result.lower():
                prefix = "ℹ️ "
            elif "ошибка" in result.lower():
                prefix = "⚠️ "
            else:
                prefix = "✅ "
            
            self.bot.reply_to(message, f"{prefix}{result}")

    def fetch_vin_data(self):
        """Запрос данных по VIN через REST API Geely"""

        url = (
            "https://services.prod.geely.perx.ru/"
            f"vin-checker-service/api/v1/technical-campaigns?vin={self.vin}"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.geely-motors.com/",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
        }

        try:
            logger.info("GET %s", url)
            resp = requests.get(url, headers=headers, timeout=15)
            logger.info(
                "Ответ получен: HTTP %s, размер %s bytes",
                resp.status_code,
                len(resp.content),
            )
            
            # API возвращает 404 когда акций нет - это нормально!
            if resp.status_code in [200, 404]:
                return self.parse_response(resp)
            else:
                return f"Ошибка: сервер Geely вернул неожиданный код {resp.status_code}"
                
        except requests.Timeout:
            logger.error("Таймаут запроса к сервису Geely")
            return None
        except requests.ConnectionError:
            logger.error("Ошибка подключения к сервису Geely")
            return None
        except requests.RequestException as exc:
            logger.error("Сетевая ошибка: %s", exc)
            return None

    def parse_response(self, response):
        """Парсинг JSON-ответа от vin-checker-service"""

        try:
            data = response.json()
        except ValueError:
            logger.error("Ответ не в формате JSON: %s", response.text[:300])
            return "Ошибка: сервис Geely вернул некорректный ответ (не JSON)"

        # Проверяем поле success
        if not data.get("success", False):
            # API вернул success=false
            errors = data.get("errors", [])
            if errors and isinstance(errors, list):
                error_msg = errors[0].get("message", "")
                if "not found" in error_msg.lower() or "empty" in error_msg.lower():
                    return "Уважаемый клиент, на Ваш автомобиль в данный момент нет действующих сервисных кампаний."
                return f"Ошибка API: {error_msg}"
            return "По VIN не найдено информации об акциях."

        # success=true, извлекаем данные
        campaigns_data = data.get("data")
        
        if not campaigns_data:
            return "По VIN не найдено информации об акциях."
        
        # Если data — это список кампаний
        if isinstance(campaigns_data, list):
            campaigns = campaigns_data
        # Если data — это объект с полем campaigns
        elif isinstance(campaigns_data, dict) and "campaigns" in campaigns_data:
            campaigns = campaigns_data["campaigns"]
        else:
            campaigns = []

        if not campaigns:
            return "Уважаемый клиент, на Ваш автомобиль в данный момент нет действующих сервисных кампаний."

        # Формируем читаемый список акций
        lines = []
        for idx, camp in enumerate(campaigns, start=1):
            if not isinstance(camp, dict):
                lines.append(f"{idx}. {camp}")
                continue

            title = camp.get("title") or camp.get("name") or "Без названия"
            desc = camp.get("description") or camp.get("details") or ""
            code = camp.get("code") or camp.get("campaignCode") or ""

            header = f"🔧 {title}"
            if code:
                header += f" (код: {code})"
            lines.append(header)

            if desc:
                lines.append(f"  {desc}")

            lines.append("")

        result = "\n".join(lines).strip()
        logger.info("Найдено кампаний: %s", len(campaigns))
        return result or "Информация об акциях получена, но не распознана."

def main():
    """Entry point with error handling"""
    try:
        checker = VinChecker()
        checker.start()
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except ValueError as e:
        logger.critical("Ошибка конфигурации: %s", e)
        raise
    except Exception as e:
        logger.critical("Критическая ошибка: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    main()

import logging
import threading


class CustomFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[41m\033[97m",  # White on Red bg
        "RESET": "\033[0m",
    }

    def format(self, record):
        # Add threadName for context
        record.threadName = f"\033[35m{threading.current_thread().name}{self.COLORS['RESET']}"  # Magenta
        # Color the levelname
        level = record.levelname.strip()
        color = self.COLORS.get(level, "")
        reset = self.COLORS["RESET"]
        record.levelname = f"{color}{level:<7}{reset}"
        # Color the logger name for visibility
        record.name = f"\033[34m{record.name}{reset}"  # Blue
        return super().format(record)


LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] [%(threadName)s] %(message)s"
handler = logging.StreamHandler()
handler.setFormatter(CustomFormatter(LOG_FORMAT))
logger = logging.getLogger("orchestrator")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(handler)

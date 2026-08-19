import logging

from colorama import Fore, Style


class ColoredFormatter(logging.Formatter):
    FORMAT = (
        "{} "
        + Fore.LIGHTBLACK_EX
        + "[%(name)s] %(asctime)s: "
        + Fore.WHITE
        + "%(message)s"
    )

    def __init__(self, color):
        self.color = color
        logging.Formatter.__init__(
            self,
            "[%(asctime)s %(name)s]: %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record):
        level_name = record.levelname
        color = ""
        level_modify = True

        if level_name == "DEBUG":
            color = Fore.LIGHTBLACK_EX
        elif level_name == "INFO":
            color = Fore.LIGHTCYAN_EX
        elif level_name == "WARNING":
            color = Fore.LIGHTYELLOW_EX
        elif level_name == "ERROR":
            color = Fore.LIGHTRED_EX
        elif level_name == "CRITICAL":
            color = Fore.LIGHTMAGENTA_EX
        else:
            level_modify = False

        if level_modify:
            level_name = f"[{level_name}]"
        if self.color:
            level_name = color + level_name + Style.RESET_ALL

        tmp_formatter = logging.Formatter(
            ColoredFormatter.FORMAT.format(level_name), datefmt="%Y-%m-%d %H:%M:%S"
        )

        return tmp_formatter.format(record)

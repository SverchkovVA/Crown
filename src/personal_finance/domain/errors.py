"""Безопасные для показа пользователю ошибки приложения."""


class FinanceError(Exception):
    """Ожидаемая ошибка без секретных значений в сообщении."""


class ValidationError(FinanceError):
    pass


class VaultError(FinanceError):
    pass


class InvalidCredentials(VaultError):
    pass


class UnsupportedFormat(VaultError):
    pass

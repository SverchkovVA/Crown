# Сторонние компоненты

Разрабатываемая программа использует сторонние библиотеки. Собственная лицензия
проекта пока не выбрана владельцем; публикация этой основы не выполнялась.

| Компонент | Назначение | Лицензирование / источник |
|---|---|---|
| CPython | Среда Python | PSF, https://www.python.org/psf/license/ |
| PySide6 Essentials, Shiboken6, Qt | Интерфейс | LGPLv3 и другие применимые условия, https://doc.qt.io/qtforpython-6/ |
| SQLAlchemy | Доступ к данным | MIT, https://github.com/sqlalchemy/sqlalchemy |
| Alembic | Миграции | MIT, https://github.com/sqlalchemy/alembic |
| sqlcipher3 | Python DB-API | Условия пакета, https://github.com/coleifer/sqlcipher3 |
| SQLCipher Community | Шифрование SQLite | BSD-style с уведомлениями, https://www.zetetic.net/sqlcipher/license/ |
| SQLite | Локальная база | Public domain, https://www.sqlite.org/copyright.html |
| PyNaCl / libsodium | Argon2id, AEAD, secretstream | Apache-2.0 / ISC, https://github.com/pyca/pynacl |
| filelock | Блокировка книги | Unlicense, https://github.com/tox-dev/filelock |
| platformdirs | Пользовательские каталоги | MIT, https://github.com/tox-dev/platformdirs |
| PyInstaller | Сборка | GPL с исключением для собранных приложений, https://pyinstaller.org/en/stable/license.html |

SQLCipher: This product includes software developed by Zetetic LLC.
Qt: This product uses the Qt framework and Qt for Python.

Используются Qt Core, Gui и Widgets. Qt Charts, Graphs, WebEngine и сетевой модуль
не подключаются приложением. Сборка имеет вид каталога; не вводятся ограничения
на замену LGPL-библиотек и исследование изменений этих библиотек.

Перед внешним распространением необходимо проверить лицензии именно собранных
DLL, OpenSSL, Qt и прочих транзитивных компонентов, вложить их полные тексты и
обеспечить выполнение применимых требований LGPL. Эта проверка ещё не завершена;
сборка этапа 3 является локальной проверочной версией.

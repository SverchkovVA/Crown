# Проверка происхождения Qt и Microsoft Runtime

Все 35 нативных файлов из пакетов PySide6 Essentials (30) и Shiboken6 (5)
в dev128 совпали по SHA-256 с байтами двух Windows-колёс версии 6.12.0.
SHA-256 и размер самих колёс сверены с `uv.lock`. В поставку входят четыре
библиотеки Qt Core, Gui, Svg и Widgets, расширения Python, платформенный
плагин Windows и плагины форматов изображений. В этой поставке исключены
сетевой плагин TUIO и Qt6Network.
[Состав нативных файлов](stage-9-windows-native-origins.md),
[развёртывание Qt на Windows](https://doc.qt.io/qt-6/windows-deployment.html)

Поле `License` колёс перечисляет открытые варианты лицензирования. В их
каталогах `dist-info/licenses` найден только `LicenseRef-Qt-Commercial.txt`.
Для целевой поставки нужно отдельно сопоставить точные варианты лицензий
модулей и плагинов, подготовить соответствующие исходники, уведомления и
проверку замены LGPL-библиотек. Документация Qt для Python перечисляет
дополнительно лицензии вложенных компонентов.
[Лицензии Qt для Python](https://doc.qt.io/qtforpython-6/licenses.html)

У шести копий `VCRUNTIME140.dll` и `VCRUNTIME140_1.dll` из CPython,
PySide6 и Shiboken6 указана версия 14.44.35211.0. Файлы имеют шесть разных
SHA-256. В этой Windows все шесть прошли Authenticode с издателем Microsoft.
Для выпуска остаётся проверить применимые условия распространения
этих файлов.
[Распространение Visual C++ Runtime](https://learn.microsoft.com/cpp/windows/redistributing-visual-cpp-files)

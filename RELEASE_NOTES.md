# v2.23.13

Совместимая Windows-сборка после ошибки загрузки python312.dll.

- Windows EXE теперь собирается на Python 3.11 вместо Python 3.12.
- Visual C++ runtime vcruntime140.dll и vcruntime140_1.dll явно включаются в standalone EXE.
- CI проверяет наличие python311.dll и vcruntime140.dll внутри PyInstaller-архива.
- Исправлена одна конструкция db.py для совместимости с Python 3.11.
- Сохранены исправления v2.23.12:
  - «Рутокен ЭЦП 3.0 3120» и «Рутокен ЭЦП 3120» считаются одним товаром;
  - вкладка «Анализ конкурентов» использует изменяемые вертикальные панели и прокрутку.
- Полный регресс 2.17.2–2.23.13, Windows build, embedded-runtime check и smoke-test пройдены.

# Сохранение функций при переходе к семестровым курсам

Исходная точка: `23b6e08`. Перечень сверяется с исходниками и исполняемыми
синтетическими проверками. Реальные БД, имена, UID и выгрузки в этот документ не входят.

| Функция оригинала | Путь после переработки | Проверка |
| --- | --- | --- |
| PC/SC: явный интерфейс, восстановление соединения, подавление удержания | Настройки → считыватель; прежний PCSCReader | `test_readers.py`: native selection, held card, reconnect, bounded stop |
| Отдельный имитатор, разделение real/demo | `--demo`, настройки; маркер режима БД | `test_readers.py`, `test_service.py`: mode marker; `test_gui.py`: no simulator in real mode |
| Неизвестная карта до импорта, поздняя привязка без сканирования | Занятие → последнее предъявление и закреплённая карта сопоставления | `test_import_card_history.py`, `test_recent_scan_feedback.py`, `test_lesson_matching_workflow.py` |
| Полный UID и ведущие нули | Внутренний ключ карты; публичное окно UID не получает | `test_service.py`: UID length/leading zeros; `test_readers.py`: native bytes |
| Уникальная отметка, неизменное первое фактическое время, повтор не означает уход | Существующие scans и card_evidence; проекция посещаемости | `test_service.py`: repeat, two cards one student; `test_redesign_service.py` |
| Сначала атомарное сохранение, потом публичный успех | AttendanceController → транзакция сервиса → публичный DTO | `test_controller.py`, `test_redesign_controller.py`, `test_gui.py`: failed commit |
| Публичное окно, очистка ФИО, безопасные сообщения | Публичное окно в постоянной полосе действий | `test_gui.py`: allowlist, timeout, colors; `test_architecture.py` |
| Исправление и снятие привязки, несколько карт | От последнего предъявления / студента, явное подтверждение | `test_service.py`: correction, unbind, stale owner, atomic multi-card unbind |
| История исправлений и сохранность предъявлений | Приватный журнал; прежние binding_changes и scans | `test_import_card_history.py`, `test_redesign_acceptance.py` |
| Ручные отметки, причина/аудит, отмена без удаления независимых оснований | Выделенные студенты/ячейки → ручная отметка → история отметки | `test_redesign_service.py`: manual evidence, unbinding, exact targets |
| Удаление ошибочного/тестового занятия с восстановлением | Расписание → удалить; список удалённых → восстановить; история и ID сохраняются | Новые lifecycle checks: весь блок, запрет активного, сохранение evidence, восстановление |
| Закрытие/возобновление, прерванная регистрация, сохранение между запусками | Постоянная полоса регистрации; восстановление при старте | `test_service.py`: close/reopen/recovery; `test_redesign_controller.py` |
| Независимые просмотр и активная регистрация, захват контекста до очереди | Верхняя полоса регистрации и отдельное выбранное занятие | `test_controller.py`: queued context; `test_recent_scan_feedback.py`: focus/pinned UID |
| Учебные списки/группы/составы, поиск и фильтры | Курс → состав / общий импорт; нечёткий поиск ФИО | `test_redesign_catalog.py`, `test_search_index.py`, `test_import_row_validation.py` |
| Сортировка с устойчивыми ID, клавиатура/Shift, история | Таблицы студентов и журнала, выбор целей по ID | `test_redesign_gui.py`, `test_redesign_acceptance.py` |
| CSV-обмен, приватный журнал отдельно от ведомости | Экспорт CSV и расширенная плоская ведомость | `test_csv.py`, `test_csv_spreadsheet_import.py`; новый экспорт оценивания |
| Согласованное резервирование и проверенное восстановление | Настройки → данные; headless migration/rehearsal/rollback | `test_service.py`, `test_redesign_migration.py`, `test_migration_cli.py`; новые migration safety tests |

Раздельный контроль двух пар, выбор охвата и оппоненты отменены спецификацией
от 9 ноября. Совместимость хранения старых пар и истории не возвращает эти действия
в повседневный курс: новая регистрация подтверждает целое занятие.

Аппаратные сценарии PC/SC проверяются управляемым нативным адаптером в тестах.
Эти проверки не заменяют проверку физического устройства на компьютере преподавателя.

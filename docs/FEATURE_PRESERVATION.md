# Сохранение функций при переходе к семестровым курсам

Исходная точка: `23b6e08`. Перечень сверяется с исходниками и исполняемыми
синтетическими проверками. Реальные БД, имена, UID и выгрузки в этот документ не входят.

| Функция оригинала | Путь после переработки | Проверка |
| --- | --- | --- |
| PC/SC: явный интерфейс, восстановление соединения, подавление удержания | Сервис → Настройки → считыватель; прежний PCSCReader | `test_readers.py`: native selection, held card, reconnect, bounded stop |
| Отдельный имитатор, разделение real/demo | `--demo`, настройки; маркер режима БД | `test_readers.py`, `test_service.py`: mode marker; `test_gui.py`: no simulator in real mode |
| Неизвестная карта до импорта, поздняя привязка без сканирования | Занятие → последнее предъявление и закреплённая карта сопоставления | `test_import_card_history.py`, `test_recent_scan_feedback.py`, `test_lesson_matching_workflow.py` |
| Полный UID и ведущие нули | Внутренний ключ карты; публичное окно UID не получает | `test_service.py`: UID length/leading zeros; `test_readers.py`: native bytes |
| Уникальная отметка, неизменное первое фактическое время, повтор не означает уход | Существующие scans и card_evidence; проекция посещаемости | `test_service.py`: repeat, two cards one student; `test_redesign_service.py` |
| Сначала атомарное сохранение, потом публичный успех | AttendanceController → транзакция сервиса → публичный DTO | `test_controller.py`, `test_redesign_controller.py`, `test_gui.py`: failed commit |
| Публичное окно, очистка ФИО, безопасные сообщения | Вид → Публичное окно | `test_gui.py`: allowlist, timeout, colors; `test_architecture.py` |
| Исправление и снятие привязки, несколько карт | От последнего предъявления / студента, явное подтверждение | `test_service.py`: correction, unbind, stale owner, atomic multi-card unbind |
| История исправлений и сохранность предъявлений | Приватный журнал; прежние binding_changes и scans | `test_import_card_history.py`, `test_redesign_acceptance.py` |
| Ручные отметки, причина/аудит, отмена без удаления независимых оснований | Выделенные студенты/ячейки → ручная отметка → история отметки; Enter, Пробел, двойной щелчок | `test_redesign_service.py`, `test_journal_marking.py`, `test_journal_cell_editing.py` |
| Удаление ошибочного/тестового занятия с восстановлением | Расписание → удалить; список удалённых → восстановить; история и ID сохраняются | Новые lifecycle checks: весь блок, запрет активного, сохранение evidence, восстановление |
| Закрытие/возобновление, прерванная регистрация, сохранение между запусками | Занятие → регистрация; активный статус сверху, восстановление при старте | `test_service.py`: close/reopen/recovery; `test_redesign_controller.py` |
| Независимые просмотр и активная регистрация, захват контекста до очереди | Верхняя полоса регистрации и отдельное выбранное занятие | `test_controller.py`: queued context; `test_recent_scan_feedback.py`: focus/pinned UID |
| Учебные списки/группы/составы, поиск и фильтры | Курс → состав / общий импорт; нечёткий поиск ФИО | `test_redesign_catalog.py`, `test_search_index.py`, `test_import_row_validation.py` |
| Сортировка с устойчивыми ID, клавиатура/Shift, история | Таблицы студентов и журнала, выбор целей по ID | `test_redesign_gui.py`, `test_redesign_acceptance.py` |
| CSV-обмен, приватный журнал отдельно от ведомости | Экспорт CSV и расширенная плоская ведомость | `test_csv.py`, `test_csv_spreadsheet_import.py`; новый экспорт оценивания |
| Согласованное резервирование и проверенное восстановление | Файл → резервная копия / восстановление; headless migration/rehearsal/rollback | `test_service.py`, `test_redesign_migration.py`, `test_migration_cli.py`; новые migration safety tests |

Дополнительно проверены основные офисные маршруты в `test_office_navigation.py`,
`test_course_workspace_toolbar.py`, `test_lesson_workspace_compact.py` и
`test_grading.py`: оценки доступны до настройки, недоступные действия не получают
ложную цель, фильтр пропусков не учитывает чужие или будущие темы. Лекции включают
весь выбранный курс обучения за учебный год; прежние снимки при этом не меняются.
Назначение групп существующей линии и резервное отображение известных посетителей
проверены в `test_course_membership.py`, `test_course_absences.py`,
`test_course_observed_students.py` и `test_course_observed_journal.py`.

Раздельный контроль двух пар, выбор охвата и оппоненты отменены спецификацией
от 9 ноября. Совместимость хранения старых пар и истории не возвращает эти действия
в повседневный курс: новая регистрация подтверждает целое занятие.

Аппаратные сценарии PC/SC проверяются управляемым нативным адаптером в тестах.
Эти проверки не заменяют проверку физического устройства на компьютере преподавателя.

## Рабочий Qt Quick / SCXML (issue #3)

Новый интерфейс запускается через `attendance-lab --qml` или `attendance-lab-quick`.
Он использует рабочие прикладные сценарии и SQLite, а не синтетический runtime.
Обычный запуск пока сохраняет QWidget: issue требует физической приёмки перед
переключением default launch и удалением fallback. В автоматических тестах данные вымышлены,
но база, транзакции, worker и QML настоящие. Дополнительно все маршруты прошли
проверку на закрытой копии существующей рабочей БД; результаты и ограничения
описаны в `VALIDATION.md`. Навигация сохраняет прежние четыре основные вкладки,
студенты и обслуживание открываются через меню отдельными окнами.

| Сохраняемая функция | Реализованный маршрут | Автоматическая проверка |
| --- | --- | --- |
| PC/SC, выбор, reconnect, удержание | Сервис → Считыватель и экран | `test_readers.py`, `test_quick_system.py`; физическая приёмка остаётся |
| Demo и изоляция real/demo | `--demo`, отдельный имитатор в Сервисе | `test_quick_system.py`, `test_service.py` |
| Неизвестная карта, позднее сопоставление | Занятие → неизвестные карты / закреплённый черновик | `test_quick_workspace.py`: late mapping после закрытия и перезапуска |
| Полный UID, ведущие нули | Занятие / Студенты → Карты | `test_quick_academic.py`, `test_service.py`; PublicProjection UID не получает |
| Уникальное присутствие, первое время | Занятие → регистрация / Журнал | `test_controller_reliability.py`, `test_quick_workspace.py`, `test_service.py` |
| Commit до публичного успеха | Worker → безопасный PublicResult | `test_quick_workspace.py`: failed commit, refresh failure, FIFO |
| Светлый публичный экран и очистка ФИО | Отдельное окно, Сервис → экран | `test_quick_runtime.py`: allowlist, PlainText, отсутствие UID/приватных данных |
| Исправление и снятие привязки, несколько карт | Занятие / Студенты → Карты | `test_quick_academic.py`, `test_quick_workspace.py`: binding revision, ABA, атомарность |
| История карт и исходных предъявлений | Студенты → История; Сервис → Архив и журнал | `test_quick_academic.py`, `test_quick_system.py` |
| Ручные отметки, причины, отмена | Посещаемость: ячейки, Enter/Пробел/двойной щелчок, история | `test_journal_contracts.py`, `test_quick_academic.py`: recheck, durable chooser, replay после revoke |
| Удаление и восстановление занятия | Курс → расписание / удалённые | `test_quick_academic.py`, `test_redesign_acceptance.py` |
| Закрытие, возобновление, восстановление после сбоя | Занятие → регистрация; запуск | `test_controller_reliability.py`, `test_quick_workspace.py`: очередь, повтор, восстановление |
| Независимость просмотра и регистрации | Выбранное занятие и отдельный статус сеанса | `test_quick_workspace.py`: frozen context, stale reads, accepted writes при shutdown |
| Учебные списки, группы, поиск, фильтры | Курс → состав; Студенты и импорт | `test_quick_academic.py`, `test_course_membership.py`, `test_search_index.py` |
| Устойчивые ID, сортировка, Shift и история | Таблицы журнала и студентов | `test_quick_academic.py`, `test_course_commands.py`; выбор хранится вне делегатов |
| CSV, XLSX, приватный журнал | Посещаемость / Оценки / Студенты → экспорт; Сервис → CSV | `test_quick_academic.py`, `test_quick_grading.py`, `test_quick_system.py`, `test_csv.py` |
| Backup, restore, миграция и rollback | Сервис → Резервные копии; прежний headless CLI | `test_quick_system.py`, `test_operation_receipts.py`, `test_migration_cli.py` |
| Импорт с preview и справочники | Студенты и импорт → CSV / Справочники | `test_quick_academic.py`: preview revision, явные решения, атомарность |
| Настройки, копирование и программа курса | Курс → настройки / копировать / темы | `test_quick_academic.py`, `test_course_commands.py` |
| Назначение состава и известные посетители | Курс → состав; Посещаемость | `test_quick_academic.py`, `test_course_observed_journal.py`, `test_course_membership.py` |
| Расписание и серии | Курс → планировать / перенести / сменить тему | `test_quick_academic.py`, `test_redesign_service.py` |
| Пропуски и неизвестный исторический состав | Посещаемость → фильтр пропусков | `test_course_absences.py`, `test_quick_academic.py`: «—», «≥», чужие и будущие темы |
| Доклады, контрольные, итоги, настройки и подбор | Оценки → три вкладки, параметры, шаблоны и диаграммы | `test_quick_grading.py`, `test_grading_preview.py`, `test_grading.py`: исходные баллы, stale revisions, ошибки, экспорт |

`test_quick_runtime.py` проверяет production SCXML, виртуальное время RxPY,
GUI affinity, освобождение ресурсов и публичную границу. `test_quick_workspace.py`
проверяет реальные worker/SQLite, ошибки и повтор команд, корреляцию, shutdown и
потерянные ответы. `test_operation_receipts.py` проверяет атомарность, перезапуск,
несовпадение payload, миграцию 4→5 и сохранность receipts в backup/restore.

Перед переключением запуска по умолчанию остаётся ручная аппаратная приёмка:
физическая карта и PC/SC (удержание, отключение/возврат, закрытие с очередью),
реальный второй монитор, фокус и горячие клавиши на целевом рабочем месте.
Автоматические тесты и offscreen-снимки этих проверок не заменяют.

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    required property var app
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    required property var windows
    readonly property var system: snapshot.system || ({})
    readonly property var registration: snapshot.registration || ({})
    readonly property var archiveStudentColumns: [
        {key: "full_name", title: "Студент", stretch: true, minimum: 170},
        {key: "group", title: "Группа", center: true}
    ]
    readonly property var archiveLesson: (system.archive || []).find(function (row) {
        return row.id === page.system.archive_lesson_id;
    }) || ({})
    readonly property bool modalOpen: saveDialog.visible || restorePicker.visible || restoreReview.visible || legacyDialog.visible || rosterDialog.visible || disciplineDialog.visible || exportStudent.pickerOpen
    function focusSearch() {
        page.openSection(1);
        archiveSearch.forceActiveFocus();
        archiveSearch.selectAll();
    }
    function openSection(index) { tabs.currentIndex = index; }
    function createBackup() { page.saveFile("backup", {}, "sqlite3"); }
    function chooseRestore() { restorePicker.open(); }
    function values(scope) {
        return app.drafts[scope] || ({});
    }
    function draft(scope, key, fallback) {
        const value = values(scope)[key];
        return value === undefined ? fallback : value;
    }
    function put(scope, key, value) {
        app.setDraft(scope, key, value);
    }
    function copy(value) {
        return JSON.parse(JSON.stringify(value));
    }
    function send(name, payload) {
        app.command("system." + name, payload || ({}));
    }
    function openArchive() {
        app.command("registration.open_legacy", {
            lesson_id: archiveLesson.id
        });
    }
    function display(value) {
        return value === null || value === undefined ? "—" : String(value);
    }
    function displayFile(value) {
        const raw = String(value);
        try {
            return decodeURIComponent(raw.replace(/^file:\/\/(?:localhost)?/, ""));
        } catch (error) {
            return raw;
        }
    }
    function saveFile(name, payload, suffix) {
        saveDialog.commandName = name;
        saveDialog.commandPayload = copy(payload);
        saveDialog.defaultSuffix = suffix;
        saveDialog.nameFilters = suffix === "sqlite3" ? ["SQLite (*.sqlite3 *.db)"] : ["CSV (*.csv)"];
        saveDialog.open();
    }
    function editLegacy(record) {
        const initial = record || ({
                timezone: "Europe/Minsk",
                title: "",
                room: ""
            });
        for (const name of ["id", "discipline_id", "starts_at", "ends_at", "timezone", "title", "room"])
            put("legacyForm", name, initial[name] === undefined ? "" : initial[name]);
        legacyDialog.open();
    }
    function saveLegacy() {
        const form = values("legacyForm");
        send("save_legacy", {
            lesson_id: form.id || null,
            discipline_id: form.discipline_id,
            starts_at: form.starts_at,
            ends_at: form.ends_at,
            timezone: form.timezone,
            title: form.title,
            room: form.room
        });
    }
    function editRoster() {
        put("legacyRoster", "lesson_id", archiveLesson.id);
        put("legacyRoster", "student_ids", copy(system.archive_roster || []));
        rosterDialog.open();
    }
    function toggleStudent(studentId, selected) {
        const ids = copy(draft("legacyRoster", "student_ids", []));
        const index = ids.indexOf(studentId);
        if (selected && index < 0)
            ids.push(studentId);
        if (!selected && index >= 0)
            ids.splice(index, 1);
        put("legacyRoster", "student_ids", ids);
    }
    function reviewRestore(path) {
        put("restore", "path", path);
        put("restore", "confirmed", false);
        restoreReview.open();
    }
    function exportReport() {
        const form = values("systemExport");
        saveFile("export_csv", {
            discipline_id: form.discipline_id,
            date_from: form.date_from || "",
            date_to: form.date_to || "",
            group: form.group || "",
            student_id: form.student_id || null,
            kind: form.kind || "matrix",
            delimiter: form.delimiter || ";",
            bom: form.bom !== false
        }, "csv");
    }
    Connections {
        target: app
        function onCommandSucceeded(name, result) {
            if (name === "system.save_legacy")
                legacyDialog.close();
            else if (name === "system.set_legacy_roster")
                rosterDialog.close();
            else if (name === "system.discipline")
                disciplineDialog.close();
            else if (name === "system.restore")
                restoreReview.close();
            else if (name === "system.backup")
                page.put("system", "last_backup", result.saved || "");
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 7
        TabBar {
            id: tabs
            Layout.fillWidth: true
            OfficeTabButton {
                text: "Считыватель и экран"
            }
            OfficeTabButton {
                text: "Архив и журнал"
            }
            OfficeTabButton {
                text: "Резервные копии"
            }
            OfficeTabButton {
                text: "Экспорт CSV"
            }
            OfficeTabButton {
                text: "Дисциплины"
            }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex
            ScrollView {
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 18
                    GroupBox {
                        title: "Считыватель карт"
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            RowLayout {
                                OfficeComboBox {
                                    id: readers
                                    Layout.fillWidth: true
                                    model: page.system.readers || []
                                    currentIndex: Math.max(0, model.indexOf(page.draft("system", "reader", "")))
                                    onActivated: page.put("system", "reader", currentText)
                                    displayText: currentIndex >= 0 ? currentText : "Нет доступных считывателей"
                                }
                                OfficeButton {
                                    text: "Обновить список"
                                    enabled: !app.busy
                                    onClicked: page.send("readers", {})
                                }
                                OfficeButton {
                                    text: "Подключить / восстановить"
                                    enabled: !app.busy && readers.currentIndex >= 0
                                    onClicked: page.send("connect_reader", {
                                        name: readers.currentText
                                    })
                                }
                            }
                            Label {
                                text: page.system.reader_status || "Считыватель не выбран"
                                Layout.fillWidth: true
                                wrapMode: Text.Wrap
                            }
                            RowLayout {
                                visible: !!page.system.demo
                                OfficeTextField {
                                    id: demoUid
                                    placeholderText: "UID для отдельного имитатора"
                                    text: page.draft("system", "demo_uid", "")
                                    onTextEdited: page.put("system", "demo_uid", text)
                                    Layout.fillWidth: true
                                }
                                OfficeButton {
                                    text: "Предъявить карту"
                                    enabled: !app.busy
                                    onClicked: page.send("simulate", {
                                        uid: demoUid.text
                                    })
                                }
                            }
                        }
                    }
                    GroupBox {
                        title: "Публичный экран"
                        Layout.fillWidth: true
                        ColumnLayout {
                            width: parent.width
                            RowLayout {
                                OfficeButton {
                                    text: windows.publicVisible ? "Скрыть публичное окно" : "Показать публичное окно"
                                    onClicked: windows.togglePublic()
                                }
                                OfficeComboBox {
                                    model: windows.screens
                                    onActivated: windows.setScreen(currentIndex)
                                    Layout.fillWidth: true
                                }
                            }
                            RowLayout {
                                CheckBox {
                                    text: "Полный экран"
                                    checked: !!page.draft("system", "public_fullscreen", false)
                                    onToggled: {
                                        page.put("system", "public_fullscreen", checked);
                                        windows.setFullscreen(checked);
                                    }
                                }
                                Label {
                                    text: "Скрыть имя через, с"
                                }
                                SpinBox {
                                    from: 1
                                    to: 60
                                    value: windows.timeoutSeconds
                                    onValueModified: windows.setTimeout(value)
                                }
                            }
                            Label {
                                text: "Публичное окно показывает только результат предъявления и автоматически скрывает имя."
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                        }
                    }
                    Label {
                        text: page.system.recovered ? "После предыдущего запуска завершено регистраций: " + page.system.recovered : ""
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
            }
            ColumnLayout {
                RowLayout {
                    OfficeSearchField {
                        id: archiveSearch
                        Layout.fillWidth: true
                        placeholderText: "Поиск занятия прежнего формата · F3"
                        text: page.draft("system", "archive_query", "")
                        onQueryEdited: query => page.put("system", "archive_query", query)
                        Keys.onDownPressed: archiveList.focusFirst()
                    }
                    OfficeButton {
                        text: "Новое прежнего формата"
                        enabled: !app.busy
                        onClicked: page.editLegacy(null)
                    }
                    OfficeButton {
                        text: "Изменить занятие"
                        enabled: !app.busy && !!page.archiveLesson.id
                        onClicked: page.editLegacy(page.archiveLesson)
                    }
                    OfficeButton {
                        text: "Изменить состав"
                        enabled: !app.busy && !!page.archiveLesson.id
                        onClicked: page.editRoster()
                    }
                }
                RowLayout {
                    OfficeButton {
                        text: "Открыть регистрацию архивного занятия"
                        enabled: app.canOpenLegacy && !!page.archiveLesson.id
                        onClicked: page.openArchive()
                    }
                    OfficeButton {
                        text: "Закрыть активную регистрацию"
                        enabled: app.canClose
                        onClicked: app.command("registration.close", ({}))
                    }
                    Label {
                        text: page.registration.active_label || "Регистрация закрыта"
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
                SplitView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    OfficeDataTable {
                        id: archiveList
                        objectName: "systemArchive"
                        SplitView.preferredWidth: 450
                        SplitView.minimumWidth: 250
                        rows: (page.system.archive || []).map(record => Object.assign({}, record, {
                            date: String(record.starts_at || "").slice(0, 10),
                            search_text: [record.id, record.title, record.starts_at, record.room, record.state].join(" ")
                        }))
                        columns: [
                            {key: "date", sortKey: "starts_at", title: "Дата", center: true},
                            {key: "title", title: "Занятие", minimum: 120, stretch: true},
                            {key: "room", title: "Аудитория", center: true}
                        ]
                        query: archiveSearch.text
                        activeId: page.system.archive_lesson_id
                        onSelected: row => page.send("archive", {lesson_id: row.id})
                        onActivated: row => page.editLegacy(row)
                    }
                    ColumnLayout {
                        SplitView.fillWidth: true
                        SplitView.minimumWidth: 300
                        TabBar {
                            id: logTabs
                            Layout.fillWidth: true
                            OfficeTabButton {
                                text: "Архивное занятие"
                            }
                            OfficeTabButton {
                                text: "Просматриваемое занятие"
                            }
                        }
                        Label {
                            text: logTabs.currentIndex === 0 ? "Архив №" + page.display(page.archiveLesson.id) : "Занятие №" + page.display(app.selectedLessonId)
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                        ListView {
                            id: privateLog
                            objectName: "systemPrivateLog"
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            model: logTabs.currentIndex === 0 ? app.systemArchiveLogRows : app.logRows
                            delegate: ItemDelegate {
                                required property var record
                                width: privateLog.width
                                text: (record.full_name || "Карта не сопоставлена") + " · " + record.uid + "\n" + record.first_seen + " → " + record.last_seen + (record.outside_roster ? " · вне состава" : "")
                            }
                            ScrollBar.vertical: ScrollBar {}
                        }
                        OfficeButton {
                            text: "Экспорт приватного журнала CSV"
                            enabled: !app.busy && !!(logTabs.currentIndex === 0 ? page.archiveLesson.id : app.selectedLessonId)
                            onClicked: page.saveFile("export_log", {
                                lesson_id: logTabs.currentIndex === 0 ? page.archiveLesson.id : app.selectedLessonId,
                                delimiter: page.draft("systemExport", "delimiter", ";"),
                                bom: page.draft("systemExport", "bom", true)
                            }, "csv")
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.alignment: Qt.AlignTop
                Label {
                    text: "Резервная копия содержит данные, предъявления, привязки и параметры приложения."
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    OfficeButton {
                        text: "Создать резервную копию…"
                        enabled: !app.busy
                        onClicked: page.saveFile("backup", {}, "sqlite3")
                    }
                    OfficeButton {
                        text: "Выбрать копию для восстановления…"
                        enabled: !app.busy
                        onClicked: restorePicker.open()
                    }
                }
                Label {
                    text: page.draft("system", "last_backup", "") ? "Копия сохранена: " + page.draft("system", "last_backup", "") : ""
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Label {
                    text: "Перед восстановлением закройте регистрацию и подтвердите сохранение принятых карт. Затем проверьте выбранный файл."
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                }
                Label {
                    text: page.registration.open ? "Регистрация сейчас открыта." : "Регистрация закрыта."
                    color: page.registration.open ? "#991b1b" : "#166534"
                }
                Label {
                    text: "Ожидают сохранения: " + (page.registration.pending_count || 0)
                }
                Item {
                    Layout.fillHeight: true
                }
            }
            ScrollView {
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    width: parent.width
                    spacing: 12
                    Label {
                        text: "Посещаемость по дисциплине"
                        font.pixelSize: Math.round(19 * (Window.window?.interfaceScale ?? 1))
                        font.bold: true
                    }
                    OfficeComboBox {
                        id: exportDiscipline
                        model: page.system.disciplines || []
                        textRole: "name"
                        valueRole: "id"
                        Layout.fillWidth: true
                        currentIndex: indexOfValue(page.draft("systemExport", "discipline_id", 0))
                        displayText: currentIndex >= 0 ? currentText : "Выберите дисциплину"
                        onActivated: page.put("systemExport", "discipline_id", currentValue)
                    }
                    RowLayout {
                        OfficeTextField {
                            placeholderText: "С даты: ГГГГ-ММ-ДД"
                            text: page.draft("systemExport", "date_from", "")
                            onTextEdited: page.put("systemExport", "date_from", text)
                            Layout.fillWidth: true
                        }
                        OfficeTextField {
                            placeholderText: "По дату: ГГГГ-ММ-ДД"
                            text: page.draft("systemExport", "date_to", "")
                            onTextEdited: page.put("systemExport", "date_to", text)
                            Layout.fillWidth: true
                        }
                    }
                    OfficeTextField {
                        placeholderText: "Группа: пусто — все"
                        text: page.draft("systemExport", "group", "")
                        onTextEdited: page.put("systemExport", "group", text)
                        Layout.fillWidth: true
                    }
                    EntityPickerField {
                        id: exportStudent
                        rows: [{id: "", full_name: "Все студенты"}].concat(page.system.archive_students || [])
                        labelField: "full_name"
                        pickerTitle: "Выберите студента"
                        searchPlaceholder: "ФИО или группа"
                        columns: page.archiveStudentColumns
                        value: page.draft("systemExport", "student_id", "")
                        onChosen: id => page.put("systemExport", "student_id", id)
                    }
                    OfficeComboBox {
                        model: [
                            {
                                label: "Матрица занятий",
                                value: "matrix"
                            },
                            {
                                label: "Только подтверждённые посещения",
                                value: "presences"
                            }
                        ]
                        textRole: "label"
                        onActivated: page.put("systemExport", "kind", model[currentIndex].value)
                    }
                    RowLayout {
                        Label {
                            text: "Разделитель"
                        }
                        OfficeComboBox {
                            model: [";", ",", "\t"]
                            currentIndex: Math.max(0, model.indexOf(page.draft("systemExport", "delimiter", ";")))
                            onActivated: page.put("systemExport", "delimiter", currentText)
                        }
                        CheckBox {
                            text: "UTF-8 с BOM"
                            checked: page.draft("systemExport", "bom", true)
                            onToggled: page.put("systemExport", "bom", checked)
                        }
                    }
                    OfficeButton {
                        text: "Экспорт посещаемости CSV…"
                        enabled: !app.busy && !!page.draft("systemExport", "discipline_id", 0)
                        onClicked: page.exportReport()
                    }
                    OfficeButton {
                        text: "Экспорт общего списка студентов CSV…"
                        enabled: !app.busy
                        onClicked: page.saveFile("export_students", {}, "csv")
                    }
                }
            }
            ColumnLayout {
                OfficeButton {
                    text: "Добавить дисциплину"
                    enabled: !app.busy
                    onClicked: disciplineDialog.open()
                }
                ListView {
                    id: disciplines
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: page.system.disciplines || []
                    delegate: ItemDelegate {
                        required property var modelData
                        width: disciplines.width
                        text: modelData.name + "\n" + modelData.description
                    }
                    ScrollBar.vertical: ScrollBar {}
                }
            }
        }
    }
    FileDialog {
        id: saveDialog
        property string commandName: ""
        property var commandPayload: ({})
        fileMode: FileDialog.SaveFile
        title: commandName === "backup" ? "Сохранить резервную копию" : "Экспорт CSV"
        onAccepted: page.send(commandName, Object.assign({}, commandPayload, {
            path: selectedFile.toString()
        }))
    }
    FileDialog {
        id: restorePicker
        title: "Выбрать резервную копию"
        fileMode: FileDialog.OpenFile
        nameFilters: ["SQLite (*.sqlite3 *.db)", "Все файлы (*)"]
        onAccepted: page.reviewRestore(selectedFile.toString())
    }
    Dialog {
        id: restoreReview
        objectName: "restoreReview"
        title: "Проверка восстановления"
        modal: true
        anchors.centerIn: parent
        width: Math.min(page.width - 30, 700)
        contentItem: ColumnLayout {
            Label {
                text: "Текущая база будет заменена содержимым выбранной резервной копии. Перед заменой приложение проверит её структуру."
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Label {
                text: "Файл:"
                font.bold: true
            }
            Label {
                objectName: "restorePath"
                text: page.displayFile(page.draft("restore", "path", ""))
                wrapMode: Text.WrapAnywhere
                textFormat: Text.PlainText
                Layout.fillWidth: true
            }
            Label {
                visible: !!page.registration.open || !!page.registration.pending_count
                text: "Сначала закройте регистрацию и подтвердите сохранение принятых карт."
                color: "#991b1b"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            CheckBox {
                text: "Подтверждаю замену текущей базы этой копией"
                checked: page.draft("restore", "confirmed", false)
                onToggled: page.put("restore", "confirmed", checked)
            }
            RowLayout {
                OfficeButton {
                    objectName: "confirmRestore"
                    text: "Восстановить выбранную копию"
                    enabled: !app.busy && !page.registration.open && !(page.registration.pending_count || 0) && page.draft("restore", "confirmed", false) && !!page.draft("restore", "path", "")
                    onClicked: page.send("restore", {
                        path: page.draft("restore", "path", "")
                    })
                }
                OfficeButton {
                    text: "Отмена"
                    onClicked: restoreReview.close()
                }
            }
        }
    }
    Dialog {
        id: legacyDialog
        objectName: "legacyDialog"
        title: page.draft("legacyForm", "id", 0) ? "Занятие прежнего формата: изменение" : "Занятие прежнего формата: создание"
        modal: true
        width: Math.min(page.width - 30, 650)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            OfficeComboBox {
                model: page.system.disciplines || []
                textRole: "name"
                valueRole: "id"
                Layout.fillWidth: true
                currentIndex: indexOfValue(page.draft("legacyForm", "discipline_id", 0))
                displayText: currentIndex >= 0 ? currentText : "Выберите дисциплину"
                onActivated: page.put("legacyForm", "discipline_id", currentValue)
            }
            OfficeTextField {
                placeholderText: "Название"
                text: page.draft("legacyForm", "title", "")
                onTextEdited: page.put("legacyForm", "title", text)
                Layout.fillWidth: true
            }
            Label {
                text: "Дата и время с часовым поясом, например 2026-09-01T09:00:00+03:00"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            OfficeTextField {
                placeholderText: "Начало"
                text: page.draft("legacyForm", "starts_at", "")
                onTextEdited: page.put("legacyForm", "starts_at", text)
                Layout.fillWidth: true
            }
            OfficeTextField {
                placeholderText: "Окончание"
                text: page.draft("legacyForm", "ends_at", "")
                onTextEdited: page.put("legacyForm", "ends_at", text)
                Layout.fillWidth: true
            }
            OfficeTextField {
                placeholderText: "Часовой пояс: Europe/Minsk"
                text: page.draft("legacyForm", "timezone", "Europe/Minsk")
                onTextEdited: page.put("legacyForm", "timezone", text)
                Layout.fillWidth: true
            }
            OfficeTextField {
                placeholderText: "Аудитория"
                text: page.draft("legacyForm", "room", "")
                onTextEdited: page.put("legacyForm", "room", text)
                Layout.fillWidth: true
            }
            RowLayout {
                OfficeButton {
                    text: "Сохранить занятие"
                    enabled: !app.busy
                    onClicked: page.saveLegacy()
                }
                OfficeButton {
                    text: "Отмена"
                    onClicked: legacyDialog.close()
                }
            }
        }
    }
    Dialog {
        id: rosterDialog
        title: "Состав архивного занятия №" + page.draft("legacyRoster", "lesson_id", "")
        modal: true
        width: Math.min(page.width - 30, 700)
        height: Math.min(page.height - 30, 700)
        anchors.centerIn: parent
        property var activeStudent: null
        onOpened: {
            rosterSearch.clear();
            archiveSelectedOnly.checked = false;
            activeStudent = null;
        }
        contentItem: ColumnLayout {
            Label {
                text: "Выберите точный состав занятия. Сохранённые предъявления не удаляются."
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                OfficeSearchField {
                    id: rosterSearch
                    placeholderText: "ФИО или группа"
                    Keys.onDownPressed: rosterStudents.focusFirst()
                }
                CheckBox { id: archiveSelectedOnly; text: "Только выбранные" }
            }
            OfficeDataTable {
                id: rosterStudents
                Layout.fillWidth: true
                Layout.fillHeight: true
                rows: archiveSelectedOnly.checked
                    ? (page.system.archive_students || []).filter(student => checkedIds.includes(student.id))
                    : (page.system.archive_students || [])
                columns: page.archiveStudentColumns
                query: rosterSearch.text
                activeId: rosterDialog.activeStudent
                checkable: true
                checkedIds: page.draft("legacyRoster", "student_ids", [])
                onSelected: row => rosterDialog.activeStudent = row.id
                onChecked: (row, value) => page.toggleStudent(row.id, value)
            }
            RowLayout {
                OfficeButton {
                    text: "Сохранить состав"
                    enabled: !app.busy
                    onClicked: page.send("set_legacy_roster", {
                        lesson_id: page.draft("legacyRoster", "lesson_id", 0),
                        student_ids: page.draft("legacyRoster", "student_ids", [])
                    })
                }
                OfficeButton {
                    text: "Отмена"
                    onClicked: rosterDialog.close()
                }
            }
        }
    }
    Dialog {
        id: disciplineDialog
        title: "Новая дисциплина"
        modal: true
        width: Math.min(page.width - 30, 500)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            OfficeTextField {
                placeholderText: "Название"
                text: page.draft("disciplineForm", "name", "")
                onTextEdited: page.put("disciplineForm", "name", text)
                Layout.fillWidth: true
            }
            TextArea {
                placeholderText: "Описание"
                text: page.draft("disciplineForm", "description", "")
                onTextChanged: if (activeFocus)
                    page.put("disciplineForm", "description", text)
                Layout.fillWidth: true
                Layout.preferredHeight: 90
            }
            RowLayout {
                OfficeButton {
                    text: "Сохранить"
                    enabled: !app.busy
                    onClicked: page.send("discipline", {
                        name: page.draft("disciplineForm", "name", ""),
                        description: page.draft("disciplineForm", "description", "")
                    })
                }
                OfficeButton {
                    text: "Отмена"
                    onClicked: disciplineDialog.close()
                }
            }
        }
    }
}

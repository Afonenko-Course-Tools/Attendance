pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    required property var app
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    property var catalogs: snapshot.catalogs || ({})
    property var preview: snapshot.import_preview || ({})
    readonly property var filters: draft("student", "filters", snapshot.student_filter || ({}))
    readonly property bool modalOpen: studentDialog.visible || importDialog.visible || catalogDialog.visible || cardDialog.visible || historyDialog.visible || importFile.visible || exportFile.visible
    function openImport() {
        importDialog.open();
    }
    function selectedStudent() {
        var id = app.selectedStudents.length ? app.selectedStudents[0] : "";
        return (snapshot.students || []).find(function (row) {
            return row.id === id;
        }) || ({});
    }
    function focusSearch() {
        search.forceActiveFocus();
        search.selectAll();
    }
    function draft(scope, key, fallback) {
        var values = app.drafts[scope] || ({});
        return (values[key] === undefined || values[key] === null) ? fallback : values[key];
    }
    function put(scope, key, value) {
        app.setDraft(scope, key, value);
        if (scope === "import" && key !== "reviewDirty" && key !== "revision") {
            app.setDraft("import", "reviewDirty", true);
            app.setDraft("import", "revision", Number(draft("import", "revision", 0)) + 1);
        }
    }
    function nameFor(rows, id) {
        var found = rows.find(function (row) {
            return row.id === id;
        });
        return found ? (found.label || found.name) : "Не выбрано";
    }
    function editStudent(record) {
        put("student", "record", record);
        for (var key of ["id", "full_name", "record_book_number", "academic_year_id", "group_id", "profile_id"])
            put("studentForm", key, record[key]);
        studentDialog.open();
    }
    function filter(extra) {
        const current = Object.assign({
            query: filters.query || "",
            study_course: filters.study_course || null,
            academic_year_id: filters.academic_year_id || null
        }, extra);
        put("student", "filters", current);
        app.command("students.filter", current);
    }
    function previewImport() {
        app.command("students.preview_import", {
            path: draft("import", "path", ""),
            token: preview.token || "",
            view_revision: Number(draft("import", "revision", 0)),
            academic_year_start: Number(draft("import", "year", new Date().getFullYear())),
            encoding: draft("import", "encoding", "utf-8-sig"),
            delimiter: draft("import", "delimiter", ";"),
            specialty_aliases: draft("import", "specialty_aliases", []),
            profile_aliases: draft("import", "profile_aliases", []),
            group_choices: draft("import", "group_choices", [])
        });
    }
    function editCards(record) {
        put("student", "record", record);
        cardDialog.open();
    }
    Connections {
        target: page.app
        ignoreUnknownSignals: true
        function onCommandSucceeded(name, result) {
            if (name === "students.save_context" || name === "students.create")
                studentDialog.close();
            if (name === "students.preview_import" && Number(result.view_revision) === Number(page.draft("import", "revision", 0)))
                page.put("import", "reviewDirty", false);
            if (name === "students.apply_import")
                importDialog.close();
            if (name === "students.unbind" || name === "students.bind")
                cardDialog.close();
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "white"
        z: -1
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 6
        RowLayout {
            spacing: 3
            ToolButton {
                text: "Добавить студента"
                onClicked: page.editStudent(({}))
            }
            ToolButton {
                text: "Изменить…"
                enabled: !!page.selectedStudent().id
                onClicked: page.editStudent(page.selectedStudent())
            }
            ToolButton {
                text: "Карты…"
                enabled: !!page.selectedStudent().id
                onClicked: page.editCards(page.selectedStudent())
            }
            ToolButton {
                text: "История…"
                enabled: !!page.selectedStudent().id
                onClicked: {
                    page.app.command("students.history", {
                        student_id: page.selectedStudent().id
                    });
                    historyDialog.open();
                }
            }
            ToolButton {
                text: "Импорт CSV"
                onClicked: page.openImport()
            }
            ToolButton {
                text: "Экспорт CSV"
                onClicked: exportFile.open()
            }
            ToolButton {
                text: "Справочники…"
                onClicked: catalogDialog.open()
            }
            Item {
                Layout.fillWidth: true
            }
        }
        RowLayout {
            spacing: 6
            OfficeSearchField {
                id: search
                Layout.fillWidth: true
                placeholderText: "ФИО, группа, специальность или № зачётки"
                text: page.filters.query || ""
                onQueryEdited: query => page.filter({query: query})
                Keys.onDownPressed: directory.focusFirst()
            }
            OfficeComboBox {
                Layout.preferredWidth: 125
                model: ["Все курсы", "1 курс", "2 курс", "3 курс", "4 курс", "5 курс", "6 курс"]
                currentIndex: page.filters.study_course || 0
                onActivated: page.filter({
                    study_course: currentIndex || null
                })
            }
            OfficeComboBox {
                Layout.preferredWidth: 190
                model: [
                    {
                        id: null,
                        name: "Все учебные годы"
                    }
                ].concat(page.catalogs.years || [])
                textRole: "name"
                valueRole: "id"
                currentIndex: Math.max(0, indexOfValue(page.filters.academic_year_id || null))
                onActivated: page.filter({
                    academic_year_id: currentValue
                })
            }
        }
        OfficeDataTable {
            id: directory
            Layout.fillWidth: true
            Layout.fillHeight: true
            rows: page.snapshot.students || []
            columns: [
                {key: "name", title: "ФИО", stretch: true, minimum: 180},
                {key: "course", title: "Курс", numeric: true, center: true},
                {key: "group_number", title: "Группа", numeric: true, center: true},
                {key: "specialty", title: "Специальность", minimum: 120},
                {key: "record_book_number", title: "№ зачётки", minimum: 95},
                {key: "card_count", title: "Карт", numeric: true, center: true}
            ]
            activeId: page.app.selectedStudents[page.app.selectedStudents.length - 1]
            selectedIds: page.app.selectedStudents
            onSelected: (row, extend) => {
                if (page.app.selectStudentInOrder)
                    page.app.selectStudentInOrder(row.id, extend, directory.orderedRows.map(item => item.id));
                else
                    page.app.selectStudent(row.id, extend);
            }
            onActivated: row => page.editStudent(row)
        }
    }
    FileDialog {
        id: importFile
        title: "Выберите CSV студентов"
        nameFilters: ["CSV (*.csv)", "Все файлы (*)"]
        onAccepted: {
            page.put("import", "path", selectedFile.toString());
            page.app.command("students.cancel_import", {
                token: page.preview.token || ""
            });
        }
    }
    FileDialog {
        id: exportFile
        title: "Экспорт студентов"
        fileMode: FileDialog.SaveFile
        nameFilters: ["CSV (*.csv)"]
        onAccepted: page.app.command("students.export", {
            path: selectedFile.toString()
        })
    }
    Dialog {
        id: studentDialog
        title: page.draft("studentForm", "id", null) ? "Изменить студента" : "Новый студент"
        modal: true
        width: 580
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "ФИО"
                text: page.draft("studentForm", "full_name", "")
                onTextEdited: page.put("studentForm", "full_name", text)
            }
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "№ зачётной книжки (ведущие нули сохраняются)"
                text: page.draft("studentForm", "record_book_number", "")
                onTextEdited: page.put("studentForm", "record_book_number", text)
            }
            Label {
                text: "Учебный год"
            }
            OfficeComboBox {
                Layout.fillWidth: true
                model: page.catalogs.years || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("studentForm", "academic_year_id", null))
                onActivated: page.put("studentForm", "academic_year_id", currentValue)
            }
            Label {
                text: "Годовая группа"
            }
            OfficeComboBox {
                Layout.fillWidth: true
                model: page.catalogs.groups || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("studentForm", "group_id", null))
                onActivated: page.put("studentForm", "group_id", currentValue)
            }
            Label {
                text: "Профилизация"
            }
            OfficeComboBox {
                Layout.fillWidth: true
                model: [
                    {
                        id: null,
                        name: "Не указана",
                        label: "Не указана"
                    }
                ].concat(page.catalogs.profiles || [])
                textRole: "label"
                valueRole: "id"
                currentIndex: Math.max(0, indexOfValue(page.draft("studentForm", "profile_id", null)))
                onActivated: page.put("studentForm", "profile_id", currentValue)
            }
            OfficeButton {
                text: "Сохранить"
                onClicked: page.app.command(page.draft("studentForm", "id", null) ? "students.save_context" : "students.create", {
                    student_id: page.draft("studentForm", "id", null),
                    full_name: page.draft("studentForm", "full_name", ""),
                    record_book_number: page.draft("studentForm", "record_book_number", ""),
                    academic_year_id: page.draft("studentForm", "academic_year_id", null),
                    group_id: page.draft("studentForm", "group_id", null),
                    profile_id: page.draft("studentForm", "profile_id", null)
                })
            }
            OfficeButton {
                text: "Отмена"
                onClicked: studentDialog.close()
            }
        }
    }
    Dialog {
        id: importDialog
        title: "Импорт студентов с предпросмотром"
        modal: true
        width: Math.min(1000, page.width - 30)
        height: Math.min(760, page.height - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            RowLayout {
                OfficeTextField {
                    Layout.fillWidth: true
                    placeholderText: "Путь к CSV"
                    text: page.draft("import", "path", "")
                    onTextEdited: {
                        page.put("import", "path", text);
                        page.app.command("students.cancel_import", {
                            token: page.preview.token || ""
                        });
                    }
                }
                OfficeButton {
                    text: "Выбрать файл"
                    onClicked: importFile.open()
                }
            }
            RowLayout {
                Label {
                    text: "Начальный год"
                }
                SpinBox {
                    from: 1900
                    to: 2200
                    editable: true
                    value: Number(page.draft("import", "year", new Date().getFullYear()))
                    onValueModified: page.put("import", "year", value)
                }
                OfficeComboBox {
                    model: ["utf-8-sig", "cp1251", "utf-16"]
                    onActivated: page.put("import", "encoding", currentText)
                }
                OfficeComboBox {
                    model: [";", ",", "\t"]
                    onActivated: page.put("import", "delimiter", currentText)
                }
                OfficeButton {
                    text: "Предпросмотр / пересчитать решения"
                    onClicked: page.previewImport()
                }
            }
            Label {
                Layout.fillWidth: true
                text: "Заголовки: №, ФИО, Специальность, Специализация/профилизация, Курс, Группа, № зачётки. Изменения сохраняются только после применения."
                wrapMode: Text.Wrap
            }
            TabBar {
                id: importTabs
                Layout.fillWidth: true
                OfficeTabButton {
                    text: "Строки и ошибки"
                }
                OfficeTabButton {
                    text: "Названия"
                }
                OfficeTabButton {
                    text: "Варианты групп"
                }
            }
            StackLayout {
                currentIndex: importTabs.currentIndex
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    Label {
                        Layout.fillWidth: true
                        color: "#a52222"
                        text: (page.preview.errors || []).join("\n")
                        wrapMode: Text.Wrap
                        visible: text.length > 0
                    }
                    Label {
                        Layout.fillWidth: true
                        text: (page.preview.changes || []).join("\n")
                        wrapMode: Text.Wrap
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        model: page.preview.rows || []
                        delegate: ItemDelegate {
                            required property var modelData
                            width: ListView.view.width
                            text: "Строка " + modelData.source_row + " · " + modelData.full_name + " · " + modelData.specialty + " / " + modelData.profile + " · " + modelData.course + " курс · группа " + modelData.group_number + " · № " + modelData.record_book_number
                        }
                        ScrollBar.vertical: ScrollBar {}
                    }
                }
                ColumnLayout {
                    Label {
                        text: "Новые названия создаются при импорте. Явно сопоставьте их, если это другое название существующего справочника."
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                    OfficeTextField {
                        id: aliasName
                        Layout.fillWidth: true
                        placeholderText: "Название специальности из CSV"
                    }
                    OfficeComboBox {
                        id: aliasSpecialty
                        Layout.fillWidth: true
                        model: page.catalogs.specialties || []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("importChoice", "specialty_id", null))
                        onActivated: page.put("importChoice", "specialty_id", currentValue)
                    }
                    OfficeButton {
                        text: "Добавить решение по специальности"
                        onClicked: {
                            var list = page.draft("import", "specialty_aliases", []).filter(function (item) {
                                return item.name !== aliasName.text;
                            });
                            list.push({
                                name: aliasName.text,
                                id: aliasSpecialty.currentValue
                            });
                            page.put("import", "specialty_aliases", list);
                        }
                    }
                    OfficeTextField {
                        id: profileAliasName
                        Layout.fillWidth: true
                        placeholderText: "Название профилизации из CSV"
                    }
                    OfficeComboBox {
                        id: aliasProfile
                        Layout.fillWidth: true
                        model: page.catalogs.profiles || []
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("importChoice", "profile_id", null))
                        onActivated: page.put("importChoice", "profile_id", currentValue)
                    }
                    OfficeButton {
                        text: "Добавить решение по профилизации"
                        onClicked: {
                            var profile = aliasProfile.model[aliasProfile.currentIndex];
                            if (profile) {
                                var list = page.draft("import", "profile_aliases", []).filter(function (item) {
                                    return item.name !== profileAliasName.text || item.specialty_id !== profile.specialty_id;
                                });
                                list.push({
                                    name: profileAliasName.text,
                                    specialty_id: profile.specialty_id,
                                    profile_id: profile.id
                                });
                                page.put("import", "profile_aliases", list);
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: page.draft("import", "specialty_aliases", []).map(function (item) {
                            return item.name + " → " + page.nameFor(page.catalogs.specialties || [], item.id);
                        }).concat(page.draft("import", "profile_aliases", []).map(function (item) {
                            return item.name + " → " + page.nameFor(page.catalogs.profiles || [], item.profile_id);
                        })).join("\n")
                        wrapMode: Text.Wrap
                    }
                    OfficeButton {
                        text: "Сбросить решения по названиям"
                        onClicked: {
                            page.put("import", "specialty_aliases", []);
                            page.put("import", "profile_aliases", []);
                        }
                    }
                    Item {
                        Layout.fillHeight: true
                    }
                }
                ColumnLayout {
                    Label {
                        Layout.fillWidth: true
                        text: "Если найдено несколько групп с одним номером, выберите конкретную годовую группу для исходной строки CSV и пересчитайте предпросмотр."
                        wrapMode: Text.Wrap
                    }
                    SpinBox {
                        id: sourceRow
                        from: 2
                        to: 100000
                        editable: true
                    }
                    OfficeComboBox {
                        id: chosenGroup
                        Layout.fillWidth: true
                        model: page.catalogs.groups || []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("importChoice", "group_id", null))
                        onActivated: page.put("importChoice", "group_id", currentValue)
                    }
                    OfficeButton {
                        text: "Выбрать для строки"
                        onClicked: {
                            var list = page.draft("import", "group_choices", []).filter(function (item) {
                                return item.source_row !== sourceRow.value;
                            });
                            list.push({
                                source_row: sourceRow.value,
                                group_id: chosenGroup.currentValue
                            });
                            page.put("import", "group_choices", list);
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        text: page.draft("import", "group_choices", []).map(function (item) {
                            return "Строка " + item.source_row + " → " + page.nameFor(page.catalogs.groups || [], item.group_id);
                        }).join("\n")
                        wrapMode: Text.Wrap
                    }
                    OfficeButton {
                        text: "Сбросить выбор групп"
                        onClicked: page.put("import", "group_choices", [])
                    }
                    Item {
                        Layout.fillHeight: true
                    }
                }
            }
            RowLayout {
                OfficeButton {
                    text: "Применить проверенный импорт"
                    enabled: !!page.preview.can_apply && !page.draft("import", "reviewDirty", true)
                    onClicked: page.app.command("students.apply_import", {
                        token: page.preview.token
                    })
                }
                OfficeButton {
                    text: "Отмена"
                    onClicked: {
                        page.app.command("students.cancel_import", {
                            token: page.preview.token || ""
                        });
                        importDialog.close();
                    }
                }
            }
        }
    }
    Dialog {
        id: catalogDialog
        title: "Справочники и годовые группы"
        modal: true
        width: 660
        height: Math.min(680, page.height - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            TabBar {
                id: catalogTabs
                Layout.fillWidth: true
                OfficeTabButton {
                    text: "Годы и группы"
                }
                OfficeTabButton {
                    text: "Названия и псевдонимы"
                }
            }
            StackLayout {
                currentIndex: catalogTabs.currentIndex
                Layout.fillWidth: true
                Layout.fillHeight: true
                ColumnLayout {
                    RowLayout {
                        SpinBox {
                            id: newYear
                            from: 1900
                            to: 2200
                            value: new Date().getFullYear()
                            editable: true
                        }
                        OfficeButton {
                            text: "Добавить год"
                            onClicked: page.app.command("students.save_year", {
                                start_year: newYear.value
                            })
                        }
                    }
                    OfficeComboBox {
                        id: groupEditor
                        Layout.fillWidth: true
                        model: [
                            {
                                id: null,
                                name: "Новая годовая группа"
                            }
                        ].concat(page.catalogs.groups || [])
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: Math.max(0, indexOfValue(page.draft("groupForm", "id", null)))
                        onActivated: {
                            var group = model[currentIndex];
                            page.put("groupForm", "id", group.id);
                            for (var key of ["academic_year_id", "specialty_id", "course", "number", "variant_key"])
                                page.put("groupForm", key, group[key]);
                        }
                    }
                    OfficeComboBox {
                        Layout.fillWidth: true
                        model: page.catalogs.years || []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("groupForm", "academic_year_id", null))
                        onActivated: page.put("groupForm", "academic_year_id", currentValue)
                    }
                    OfficeComboBox {
                        Layout.fillWidth: true
                        model: page.catalogs.specialties || []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("groupForm", "specialty_id", null))
                        onActivated: page.put("groupForm", "specialty_id", currentValue)
                    }
                    RowLayout {
                        Label {
                            text: "Курс"
                        }
                        SpinBox {
                            from: 1
                            to: 6
                            value: page.draft("groupForm", "course", 1)
                            onValueModified: page.put("groupForm", "course", value)
                        }
                        Label {
                            text: "Группа"
                        }
                        SpinBox {
                            from: 1
                            to: 1000
                            value: page.draft("groupForm", "number", 1)
                            onValueModified: page.put("groupForm", "number", value)
                        }
                    }
                    OfficeTextField {
                        Layout.fillWidth: true
                        placeholderText: "Уточнение варианта группы"
                        text: page.draft("groupForm", "variant_key", "")
                        onTextEdited: page.put("groupForm", "variant_key", text)
                    }
                    OfficeButton {
                        text: "Сохранить группу"
                        onClicked: page.app.command("students.save_group", {
                            group_id: page.draft("groupForm", "id", null),
                            academic_year_id: page.draft("groupForm", "academic_year_id", null),
                            specialty_id: page.draft("groupForm", "specialty_id", null),
                            study_course: page.draft("groupForm", "course", 1),
                            number: page.draft("groupForm", "number", 1),
                            variant_key: page.draft("groupForm", "variant_key", "")
                        })
                    }
                    Item {
                        Layout.fillHeight: true
                    }
                }
                ColumnLayout {
                    OfficeComboBox {
                        id: specialtyEditor
                        Layout.fillWidth: true
                        model: page.catalogs.specialties || []
                        textRole: "name"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("catalogForm", "specialty_id", null))
                        onActivated: page.put("catalogForm", "specialty_id", currentValue)
                    }
                    OfficeTextField {
                        id: specialtyText
                        Layout.fillWidth: true
                        placeholderText: "Новое название / псевдоним специальности"
                    }
                    RowLayout {
                        OfficeButton {
                            text: "Переименовать"
                            onClicked: page.app.command("students.rename_specialty", {
                                specialty_id: specialtyEditor.currentValue,
                                name: specialtyText.text
                            })
                        }
                        OfficeButton {
                            text: "Добавить псевдоним"
                            onClicked: page.app.command("students.specialty_alias", {
                                specialty_id: specialtyEditor.currentValue,
                                name: specialtyText.text
                            })
                        }
                    }
                    OfficeComboBox {
                        id: profileEditor
                        Layout.fillWidth: true
                        model: page.catalogs.profiles || []
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: indexOfValue(page.draft("catalogForm", "profile_id", null))
                        onActivated: page.put("catalogForm", "profile_id", currentValue)
                    }
                    OfficeTextField {
                        id: profileText
                        Layout.fillWidth: true
                        placeholderText: "Новое название / псевдоним профилизации"
                    }
                    RowLayout {
                        OfficeButton {
                            text: "Переименовать"
                            onClicked: page.app.command("students.rename_profile", {
                                profile_id: profileEditor.currentValue,
                                name: profileText.text
                            })
                        }
                        OfficeButton {
                            text: "Добавить псевдоним"
                            onClicked: page.app.command("students.profile_alias", {
                                profile_id: profileEditor.currentValue,
                                name: profileText.text
                            })
                        }
                    }
                    Item {
                        Layout.fillHeight: true
                    }
                }
            }
            OfficeButton {
                text: "Закрыть"
                onClicked: catalogDialog.close()
            }
        }
    }
    Dialog {
        id: cardDialog
        title: "Карты студента"
        modal: true
        width: 600
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            Label {
                text: page.draft("student", "record", ({})).name || ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Label {
                text: "Проверенные карты: " + (page.draft("student", "record", ({})).card_uids || []).join(", ")
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            OfficeButton {
                text: "Снять привязку всех перечисленных карт"
                enabled: (page.draft("student", "record", ({})).card_uids || []).length > 0
                onClicked: {
                    var record = page.draft("student", "record", ({}));
                    page.app.command("students.unbind", {
                        student_id: record.id,
                        expected_cards: record.cards
                    });
                }
            }
            OfficeTextField {
                id: uid
                Layout.fillWidth: true
                placeholderText: "Полный UID новой карты (включая ведущие нули)"
            }
            OfficeButton {
                text: "Привязать свободную карту"
                onClicked: page.app.command("students.bind", {
                    student_id: page.draft("student", "record", ({})).id,
                    uid: uid.text,
                    binding_revision: ((page.snapshot.cards || []).find(function (card) {
                            return card.uid === uid.text.trim().toUpperCase();
                        }) || {}).binding_revision || 0
                })
            }
            Label {
                text: "Исправление чужой привязки выполняется от конкретного предъявления на странице занятия."
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            OfficeButton {
                text: "Закрыть"
                onClicked: cardDialog.close()
            }
        }
    }
    Dialog {
        id: historyDialog
        title: "История карты и предъявлений"
        modal: true
        width: Math.min(840, page.width - 30)
        height: Math.min(650, page.height - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            Label {
                text: "Изменения привязки"
                font.bold: true
            }
            ListView {
                Layout.fillWidth: true
                Layout.preferredHeight: 200
                clip: true
                model: (page.snapshot.student_history || {}).bindings || []
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.changed_at + " · " + modelData.uid + " · " + (modelData.previous_student_id || "свободна") + " → " + (modelData.student_id || "свободна")
                }
                ScrollBar.vertical: ScrollBar {}
            }
            Label {
                text: "Сохранённые предъявления"
                font.bold: true
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: (page.snapshot.student_history || {}).events || []
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.first_seen + " · " + modelData.lesson_name + " · UID " + modelData.uid
                }
                ScrollBar.vertical: ScrollBar {}
            }
            OfficeButton {
                text: "Закрыть"
                onClicked: historyDialog.close()
            }
        }
    }
}

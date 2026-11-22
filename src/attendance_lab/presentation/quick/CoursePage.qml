pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: page
    required property var app
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    signal studentsRequested
    signal writtenWorkRequested
    signal lessonRequested(int lessonId)
    readonly property bool modalOpen: courseDialog.visible || copyDialog.visible || topicDialog.visible || scheduleDialog.visible || rosterDialog.visible || deleteDialog.visible || restoreDialog.visible
    function focusSearch() {
        page.put("course", "tab", 0);
        topicSearch.forceActiveFocus();
        topicSearch.selectAll();
    }
    property var course: snapshot.course || ({})
    property var catalogs: snapshot.catalogs || ({})
    function selectedLesson() {
        return (snapshot.lessons || []).find(function (item) {
            return item.id === page.draft("course", "schedule_id", page.app.selectedLessonId);
        }) || ({});
    }
    function selectedTopic() {
        var id = draft("course", "topic", ({})).id;
        return (snapshot.topics || []).find(function (item) {
            return item.id === id;
        }) || ({});
    }
    function selectedRoster() {
        var id = draft("course", "roster", ({})).id;
        return (snapshot.rosters || []).find(function (item) {
            return item.id === id;
        }) || ({});
    }
    function draft(scope, key, fallback) {
        var values = app.drafts[scope] || ({});
        return (values[key] === undefined || values[key] === null) ? fallback : values[key];
    }
    function put(scope, key, value) {
        app.setDraft(scope, key, value);
    }
    function coursePayload(extra) {
        return Object.assign({
            course_id: app.selectedCourseId
        }, extra);
    }
    function chooseTopic(record) {
        put("course", "topic", record);
    }
    function editTopic(topic) {
        put("topic", "id", topic.id);
        put("topic", "number", topic.number);
        put("topic", "title", topic.title);
        put("topic", "batch", false);
        topicDialog.open();
    }
    function groupNames(ids) {
        const groups = new Map((catalogs.groups || []).map(group => [group.id, group]));
        return ids.filter(id => groups.has(id)).map(id => {
            const group = groups.get(id);
            return group.specialty_name + " · гр. " + group.number;
        }).join("; ");
    }
    function chooseRoster(record) {
        put("course", "roster", record);
        put("roster", "name", record.name);
        put("roster", "group_ids", record.group_ids);
        put("roster", "student_ids", record.student_ids);
        put("roster", "mode", record.mode);
    }
    function editCourse(edit) {
        var source = edit ? course : ({});
        for (var key of ["id", "discipline_id", "academic_year_id", "semester", "study_course", "kind", "duration", "name", "reports_enabled"])
            put("courseForm", key, source[key]);
        courseDialog.open();
    }
    function openSchedule(mode, occurrence) {
        put("schedule", "mode", mode);
        var lesson = occurrence || selectedLesson();
        scheduleDialog.target = lesson;
        if (mode === "change_topic") {
            put("schedule", "topic_id", lesson.topic_id);
            put("schedule", "new_topic_number", null);
            put("schedule", "new_topic_title", "");
        }
        if (mode !== "schedule") {
            put("schedule", "date", lesson.date || "");
            put("schedule", "start_slot", lesson.start_slot || 1);
        }
        scheduleDialog.open();
    }
    function openDelete(lesson) {
        if (!lesson || !lesson.can_delete)
            return;
        deleteDialog.target = lesson;
        deleteDialog.open();
    }
    function submitSchedule() {
        var mode = draft("schedule", "mode", "schedule");
        var lesson = scheduleDialog.target;
        var values = coursePayload({
            lesson_id: lesson.id,
            date: draft("schedule", "date", ""),
            start_slot: Number(draft("schedule", "start_slot", 1)),
            interval_days: Number(draft("schedule", "interval_days", 0)),
            count: Number(draft("schedule", "count", 1)),
            roster_id: draft("schedule", "roster_id", null),
            topic_id: draft("schedule", "topic_id", null),
            new_topic_number: draft("schedule", "new_topic_number", null),
            new_topic_title: draft("schedule", "new_topic_title", ""),
            room: draft("schedule", "room", "")
        });
        app.command("academic." + mode, values);
    }
    Connections {
        target: page.app
        ignoreUnknownSignals: true
        function onCommandSucceeded(name, result) {
            if (result._context_course_id !== undefined && result._context_course_id !== page.app.selectedCourseId && name !== "academic.save_course" && name !== "academic.copy_course")
                return;
            if (name === "academic.save_course")
                courseDialog.close();
            if (name === "academic.copy_course")
                copyDialog.close();
            if (["academic.schedule", "academic.move", "academic.recurrence", "academic.change_topic"].indexOf(name) >= 0)
                scheduleDialog.close();
            if (name === "academic.topic" || name === "academic.add_topics")
                topicDialog.close();
            if (["academic.add_groups", "academic.assign_groups", "academic.save_roster"].indexOf(name) >= 0)
                rosterDialog.close();
            if (name === "academic.delete")
                deleteDialog.close();
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "white"
        z: -1
    }
    component OfficeTab: OfficeTabButton {
        id: officeTab
        leftPadding: 16
        rightPadding: 16
        topPadding: 8
        bottomPadding: 8
        contentItem: Label {
            text: officeTab.text
            color: officeTab.checked ? "#1455a0" : "#384b67"
            font.weight: officeTab.checked ? Font.DemiBold : Font.Normal
            horizontalAlignment: Text.AlignHCenter
        }
        background: Rectangle {
            color: officeTab.checked ? "white" : "#e7ecf4"
        }
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: 6
        RowLayout {
            spacing: 3
            ToolButton {
                text: "Новый курс"
                onClicked: page.editCourse(false)
            }
            ToolButton {
                text: "Настройки"
                enabled: !!page.course.id
                onClicked: page.editCourse(true)
            }
            ToolButton {
                text: "Ещё ▾"
                onClicked: courseMore.open()
                Menu {
                    id: courseMore
                    MenuItem {
                        text: "Копировать в другой семестр…"
                        enabled: !!page.course.id
                        onTriggered: copyDialog.open()
                    }
                    MenuItem {
                        text: "Добавить письменную работу…"
                        enabled: !!page.course.id
                        onTriggered: page.writtenWorkRequested()
                    }
                }
            }
            Item {
                Layout.fillWidth: true
            }
        }
        Label {
            Layout.fillWidth: true
            text: page.course.id ? ((page.course.kind === "lecture" ? "Лекции · общий поток" : "Общая программа для всех групп") + " · " + page.course.duration + (page.course.duration === 1 ? " пара на занятие" : " пары на занятие")) : "Создайте курс, затем добавьте темы и учебные группы."
            color: "#17243c"
            wrapMode: Text.Wrap
        }
        RowLayout {
            spacing: 0
            TabBar {
                id: tabs
                currentIndex: Number(page.draft("course", "tab", 0))
                OfficeTab {
                    text: "Программа"
                    onClicked: page.put("course", "tab", 0)
                }
                OfficeTab {
                    text: "Состав"
                    onClicked: page.put("course", "tab", 1)
                }
                OfficeTab {
                    text: "Расписание"
                    onClicked: page.put("course", "tab", 2)
                }
            }
            ToolButton {
                text: "Учебный список и каталоги"
                onClicked: page.studentsRequested()
            }
            Item {
                Layout.fillWidth: true
            }
        }
        StackLayout {
            currentIndex: tabs.currentIndex
            Layout.fillWidth: true
            Layout.fillHeight: true
            ColumnLayout {
                spacing: 6
                RowLayout {
                    spacing: 3
                    ToolButton {
                        text: "Добавить тему"
                        enabled: !!page.course.id
                        onClicked: {
                            page.put("topic", "id", null);
                            page.put("topic", "batch", false);
                            topicDialog.open();
                        }
                    }
                    ToolButton {
                        text: "Изменить название"
                        enabled: !!page.selectedTopic().id
                        onClicked: page.editTopic(page.selectedTopic())
                    }
                    ToolButton {
                        text: "Добавить темы…"
                        enabled: !!page.course.id
                        onClicked: {
                            page.put("topic", "id", null);
                            page.put("topic", "batch", true);
                            topicDialog.open();
                        }
                    }
                    OfficeSearchField {
                        id: topicSearch
                        placeholderText: "Номер или название темы · F3"
                        Keys.onDownPressed: topicTable.focusFirst()
                    }
                }
                OfficeDataTable {
                    id: topicTable
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    rows: page.snapshot.topics || []
                    columns: [
                        {key: "number", title: "№", numeric: true, center: true},
                        {key: "title", title: "Тема", stretch: true, minimum: 160}
                    ]
                    query: topicSearch.text
                    activeId: page.selectedTopic().id
                    onSelected: row => page.chooseTopic(row)
                    onActivated: row => page.editTopic(row)
                }
            }
            ColumnLayout {
                spacing: 6
                RowLayout {
                    spacing: 3
                    visible: page.course.kind !== "lecture"
                    ToolButton {
                        text: "Добавить группы"
                        visible: page.course.kind !== "lecture"
                        enabled: !!page.course.id
                        onClicked: {
                            page.put("roster", "operation", "add_groups");
                            page.put("roster", "group_ids", []);
                            page.put("roster", "student_ids", []);
                            page.put("roster", "name", "");
                            page.put("roster", "mode", "groups");
                            rosterDialog.open();
                        }
                    }
                    ToolButton {
                        text: "Подгруппа"
                        visible: page.course.kind !== "lecture"
                        enabled: !!page.course.id
                        onClicked: {
                            page.put("roster", "operation", "add_groups");
                            page.put("roster", "group_ids", []);
                            page.put("roster", "student_ids", []);
                            page.put("roster", "name", "");
                            page.put("roster", "mode", "students");
                            rosterDialog.open();
                        }
                    }
                    ToolButton {
                        text: "Изменить состав…"
                        visible: page.course.kind !== "lecture"
                        enabled: !!page.selectedRoster().id
                        onClicked: {
                            page.put("roster", "operation", "assign_groups");
                            rosterDialog.open();
                        }
                    }
                    ToolButton {
                        text: "Участники…"
                        visible: page.course.kind !== "lecture"
                        enabled: !!page.selectedRoster().id
                        onClicked: {
                            page.put("roster", "operation", "save_roster");
                            rosterDialog.open();
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                }
                OfficeDataTable {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    rows: {
                        const students = page.snapshot.course_students || [];
                        if (page.course.kind === "lecture")
                            return [{
                                id: -1,
                                name: "Общий поток",
                                groups: page.groupNames(Array.from(new Set(students.map(student => student.group_id)))),
                                student_count: students.length
                            }];
                        return (page.snapshot.rosters || []).map(roster => Object.assign({}, roster, {
                            groups: page.groupNames(roster.group_ids || []),
                            student_count: roster.mode === "students" ? (roster.student_ids || []).length
                                : students.filter(student => (roster.group_ids || []).includes(student.group_id)).length
                        }));
                    }
                    columns: [
                        {key: "name", title: page.course.kind === "lecture" ? "Поток" : "Состав", minimum: 150},
                        {key: "groups", title: "Группы", stretch: true},
                        {key: "student_count", title: "Студентов", numeric: true, center: true}
                    ]
                    activeId: page.selectedRoster().id
                    onSelected: row => { if (row.id > 0) page.chooseRoster(row); }
                }
            }
            ColumnLayout {
                spacing: 6
                RowLayout {
                    spacing: 3
                    ToolButton {
                        text: "Добавить занятие"
                        enabled: !!((page.snapshot.commands || {}).schedule || {}).enabled
                        onClicked: page.openSchedule("schedule")
                    }
                    ToolButton {
                        text: "→ Перейти к занятию"
                        enabled: !!page.selectedLesson().id
                        onClicked: page.lessonRequested(page.selectedLesson().id)
                    }
                    ToolButton {
                        text: "Тема занятия…"
                        enabled: !!page.selectedLesson().id
                        onClicked: page.openSchedule("change_topic")
                    }
                    ToolButton {
                        text: "Изменить дату и время…"
                        enabled: !!page.selectedLesson().id
                        onClicked: page.openSchedule("move")
                    }
                    ToolButton {
                        text: "Ещё ▾"
                        onClicked: scheduleMore.open()
                        Menu {
                            id: scheduleMore
                            MenuItem {
                                text: "Удалить выбранное занятие…"
                                enabled: !!page.selectedLesson().can_delete
                                onTriggered: page.openDelete(page.selectedLesson())
                            }
                            MenuItem {
                                text: "Удалённые занятия…"
                                enabled: !!((page.snapshot.commands || {}).restore || {}).enabled
                                onTriggered: restoreDialog.open()
                            }
                        }
                    }
                    Item {
                        Layout.fillWidth: true
                    }
                }
                OfficeSearchField {
                    id: scheduleSearch
                    placeholderText: "Дата, тема, группа или аудитория"
                    Keys.onDownPressed: scheduleTable.focusFirst()
                }
                OfficeDataTable {
                    id: scheduleTable
                    removalEnabled: true
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    rows: (page.snapshot.lessons || []).map(lesson => Object.assign({}, lesson, {
                        date_label: String(lesson.date || "").split("-").reverse().join("."),
                        search_text: [lesson.date, lesson.name, lesson.room].join(" ")
                    }))
                    columns: [
                        {key: "date_label", sortKey: "starts_at", title: "Дата", center: true},
                        {key: "time", title: "Время", center: true},
                        {key: "topic", title: "Тема", minimum: 150, stretch: true}
                    ].concat(page.course.kind === "lecture" ? [] : [{key: "group", title: "Состав", minimum: 80, center: true}])
                    .concat([{key: "room", title: "Ауд.", center: true}])
                    query: scheduleSearch.text
                    activeId: page.selectedLesson().id
                    onSelected: row => page.put("course", "schedule_id", row.id)
                    onActivated: row => page.openSchedule("move", row)
                    onRemovalRequested: row => page.openDelete(row)
                }
            }
        }
    }
    Dialog {
        id: restoreDialog
        title: "Удалённые занятия"
        modal: true
        width: Math.min(760, page.width - 30)
        height: Math.min(500, page.height - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.snapshot.deletions || []
                delegate: ItemDelegate {
                    required property var modelData
                    width: ListView.view.width
                    text: modelData.name + " · " + modelData.reason + " · Восстановить"
                    onClicked: page.app.command("academic.restore", {
                        deletion_id: modelData.id
                    })
                }
                ScrollBar.vertical: ScrollBar {}
            }
            OfficeButton {
                text: "Закрыть"
                onClicked: restoreDialog.close()
            }
        }
    }
    Dialog {
        id: courseDialog
        title: page.draft("courseForm", "id", null) ? "Настройки курса" : "Новый курс"
        modal: true
        width: Math.min(580, page.width - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Название курса"
                text: page.draft("courseForm", "name", "")
                onTextEdited: page.put("courseForm", "name", text)
            }
            Label {
                text: "Дисциплина"
            }
            OfficeComboBox {
                Layout.fillWidth: true
                model: page.catalogs.disciplines || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("courseForm", "discipline_id", null))
                onActivated: page.put("courseForm", "discipline_id", currentValue)
            }
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Или название новой дисциплины"
                text: page.draft("courseForm", "discipline_name", "")
                onTextEdited: {
                    page.put("courseForm", "discipline_name", text);
                    page.put("courseForm", "discipline_id", null);
                }
            }
            Label {
                text: "Учебный год"
            }
            OfficeComboBox {
                Layout.fillWidth: true
                model: page.catalogs.years || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("courseForm", "academic_year_id", null))
                onActivated: page.put("courseForm", "academic_year_id", currentValue)
            }
            RowLayout {
                Label {
                    text: "Семестр"
                }
                SpinBox {
                    from: 1
                    to: 2
                    value: page.draft("courseForm", "semester", 1)
                    onValueModified: page.put("courseForm", "semester", value)
                }
                Label {
                    text: "Курс обучения"
                }
                SpinBox {
                    from: 1
                    to: 6
                    value: page.draft("courseForm", "study_course", 1)
                    onValueModified: page.put("courseForm", "study_course", value)
                }
            }
            OfficeComboBox {
                model: ["lecture", "seminar", "lab", "other"]
                currentIndex: Math.max(0, model.indexOf(page.draft("courseForm", "kind", "lecture")))
                onActivated: page.put("courseForm", "kind", currentText)
            }
            RowLayout {
                Label {
                    text: "Пар в занятии"
                }
                SpinBox {
                    from: 1
                    to: 2
                    value: page.draft("courseForm", "duration", 1)
                    onValueModified: page.put("courseForm", "duration", value)
                }
            }
            CheckBox {
                text: "Доклады включены"
                checked: page.draft("courseForm", "reports_enabled", false)
                onToggled: page.put("courseForm", "reports_enabled", checked)
            }
            OfficeButton {
                text: "Сохранить"
                onClicked: page.app.command("academic.save_course", {
                    course_id: page.draft("courseForm", "id", null),
                    discipline_id: page.draft("courseForm", "discipline_id", null),
                    discipline_name: page.draft("courseForm", "discipline_name", ""),
                    academic_year_id: page.draft("courseForm", "academic_year_id", null),
                    semester: page.draft("courseForm", "semester", 1),
                    study_course: page.draft("courseForm", "study_course", 1),
                    kind: page.draft("courseForm", "kind", "lecture"),
                    duration: page.draft("courseForm", "duration", 1),
                    name: page.draft("courseForm", "name", ""),
                    reports_enabled: page.draft("courseForm", "reports_enabled", false)
                })
            }
            OfficeButton {
                text: "Отмена"
                onClicked: courseDialog.close()
            }
        }
    }
    Dialog {
        id: copyDialog
        title: "Копировать курс"
        modal: true
        width: 460
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Название копии"
                text: page.draft("copy", "name", "")
                onTextEdited: page.put("copy", "name", text)
            }
            OfficeComboBox {
                model: page.catalogs.years || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("copy", "academic_year_id", page.course.academic_year_id))
                onActivated: page.put("copy", "academic_year_id", currentValue)
            }
            SpinBox {
                from: 1
                to: 2
                value: page.draft("copy", "semester", 1)
                onValueModified: page.put("copy", "semester", value)
            }
            OfficeButton {
                text: "Создать копию"
                onClicked: page.app.command("academic.copy_course", page.coursePayload({
                    name: page.draft("copy", "name", ""),
                    academic_year_id: page.draft("copy", "academic_year_id", page.course.academic_year_id),
                    semester: page.draft("copy", "semester", 1)
                }))
            }
            OfficeButton {
                text: "Отмена"
                onClicked: copyDialog.close()
            }
        }
    }
    Dialog {
        id: topicDialog
        title: "Тема программы"
        modal: true
        width: 480
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            RowLayout {
                Label {
                    text: "Номер / первый номер"
                }
                SpinBox {
                    from: 1
                    to: 10000
                    enabled: !page.draft("topic", "id", null)
                    value: page.draft("topic", "number", 1)
                    onValueModified: page.put("topic", "number", value)
                }
            }
            OfficeTextField {
                Layout.fillWidth: true
                visible: !page.draft("topic", "batch", false)
                placeholderText: "Название темы"
                text: page.draft("topic", "title", "")
                onTextEdited: page.put("topic", "title", text)
            }
            RowLayout {
                visible: page.draft("topic", "batch", false)
                Label {
                    text: "Число тем"
                }
                SpinBox {
                    from: 1
                    to: 1000
                    value: page.draft("topic", "count", 1)
                    onValueModified: page.put("topic", "count", value)
                }
            }
            OfficeButton {
                text: "Сохранить"
                onClicked: page.app.command(page.draft("topic", "batch", false) ? "academic.add_topics" : "academic.topic", page.coursePayload({
                    topic_id: page.draft("topic", "id", null),
                    number: page.draft("topic", "number", 1),
                    first_number: page.draft("topic", "number", 1),
                    count: page.draft("topic", "count", 1),
                    title: page.draft("topic", "title", "")
                }))
            }
            OfficeButton {
                text: "Отмена"
                onClicked: topicDialog.close()
            }
        }
    }
    Dialog {
        id: scheduleDialog
        property var target: ({})
        readonly property string mode: page.draft("schedule", "mode", "schedule")
        readonly property string lockReason: mode === "move" ? (target.move_reason || "") : ""
        title: mode === "move" ? "Редактировать занятие" : mode === "change_topic" ? "Тема занятия" : "Запланировать занятие"
        modal: true
        width: 560
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            Label {
                visible: !!scheduleDialog.lockReason
                text: scheduleDialog.lockReason
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            Label {
                text: "Дата · ГГГГ-ММ-ДД"
            }
            OfficeTextField {
                Layout.fillWidth: true
                text: page.draft("schedule", "date", "")
                readOnly: !!scheduleDialog.lockReason
                placeholderText: "2026-09-01"
                onTextEdited: page.put("schedule", "date", text)
            }
            RowLayout {
                Label {
                    text: "Начальная пара"
                }
                SpinBox {
                    from: 1
                    to: page.snapshot.max_start_slot || 8
                    enabled: !scheduleDialog.lockReason
                    value: page.draft("schedule", "start_slot", 1)
                    onValueModified: page.put("schedule", "start_slot", value)
                }
            }
            Label {
                text: "Тема программы"
                visible: ["schedule", "change_topic"].indexOf(page.draft("schedule", "mode", "schedule")) >= 0
            }
            OfficeComboBox {
                Layout.fillWidth: true
                visible: ["schedule", "change_topic"].indexOf(page.draft("schedule", "mode", "schedule")) >= 0
                model: page.snapshot.topics || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("schedule", "topic_id", null))
                onActivated: page.put("schedule", "topic_id", currentValue)
            }
            RowLayout {
                visible: ["schedule", "change_topic"].indexOf(page.draft("schedule", "mode", "schedule")) >= 0
                OfficeTextField {
                    Layout.preferredWidth: 100
                    placeholderText: "Новый №"
                    text: page.draft("schedule", "new_topic_number", "")
                    onTextEdited: {
                        page.put("schedule", "new_topic_number", text);
                        page.put("schedule", "topic_id", null);
                    }
                }
                OfficeTextField {
                    Layout.fillWidth: true
                    placeholderText: "Или новая тема"
                    text: page.draft("schedule", "new_topic_title", "")
                    onTextEdited: {
                        page.put("schedule", "new_topic_title", text);
                        page.put("schedule", "topic_id", null);
                    }
                }
            }
            OfficeComboBox {
                Layout.fillWidth: true
                visible: page.course.kind !== "lecture" && page.draft("schedule", "mode", "schedule") === "schedule"
                model: page.snapshot.rosters || []
                textRole: "name"
                valueRole: "id"
                currentIndex: indexOfValue(page.draft("schedule", "roster_id", null))
                onActivated: page.put("schedule", "roster_id", currentValue)
            }
            RowLayout {
                visible: scheduleDialog.mode === "schedule"
                Label {
                    text: "Повтор"
                }
                OfficeComboBox {
                    model: [
                        {
                            name: "Без повтора",
                            days: 0
                        },
                        {
                            name: "7 дней",
                            days: 7
                        },
                        {
                            name: "14 дней",
                            days: 14
                        }
                    ]
                    textRole: "name"
                    currentIndex: Math.max(0, [0, 7, 14].indexOf(Number(page.draft("schedule", "interval_days", 0))))
                    onActivated: page.put("schedule", "interval_days", model[currentIndex].days)
                }
                SpinBox {
                    from: 1
                    to: 100
                    value: page.draft("schedule", "count", 1)
                    onValueModified: page.put("schedule", "count", value)
                }
            }
            OfficeTextField {
                Layout.fillWidth: true
                visible: scheduleDialog.mode === "schedule"
                placeholderText: "Аудитория"
                text: page.draft("schedule", "room", "")
                onTextEdited: page.put("schedule", "room", text)
            }
            OfficeButton {
                visible: !scheduleDialog.lockReason
                text: "Сохранить"
                onClicked: page.submitSchedule()
            }
            OfficeButton {
                text: scheduleDialog.lockReason ? "Закрыть" : "Отмена"
                onClicked: scheduleDialog.close()
            }
        }
    }
    Dialog {
        id: rosterDialog
        title: "Учебный состав"
        modal: true
        width: Math.min(790, page.width - 30)
        height: Math.min(680, page.height - 30)
        anchors.centerIn: parent
        property var activeGroup: null
        property var activeStudent: null
        onOpened: {
            groupsSearch.clear();
            rosterSearch.clear();
            selectedOnly.checked = false;
            activeGroup = null;
            activeStudent = null;
        }
        function changeChoice(key, id, checked) {
            const ids = page.draft("roster", key, []).filter(value => value !== id);
            page.put("roster", key, checked ? ids.concat([id]) : ids);
        }
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Название линии"
                text: page.draft("roster", "name", "")
                onTextEdited: page.put("roster", "name", text)
            }
            OfficeComboBox {
                model: [
                    {
                        name: "Целые группы",
                        value: "groups"
                    },
                    {
                        name: "Подгруппа выбранных студентов",
                        value: "students"
                    }
                ]
                textRole: "name"
                currentIndex: page.draft("roster", "mode", "groups") === "students" ? 1 : 0
                onActivated: page.put("roster", "mode", model[currentIndex].value)
            }
            OfficeSearchField {
                id: groupsSearch
                placeholderText: "Поиск групп"
                Keys.onDownPressed: groupsTable.focusFirst()
            }
            OfficeDataTable {
                id: groupsTable
                Layout.fillWidth: true
                Layout.fillHeight: page.draft("roster", "mode", "groups") !== "students"
                Layout.preferredHeight: Math.round(130 * page.interfaceScale)
                rows: page.snapshot.course_groups || []
                columns: [
                    {key: "name", title: "Группа", stretch: true, center: true, sortKey: "number", numeric: true}
                ]
                sortField: "name"
                query: groupsSearch.text
                checkable: true
                activeId: rosterDialog.activeGroup
                checkedIds: page.draft("roster", "group_ids", [])
                onSelected: row => rosterDialog.activeGroup = row.id
                onChecked: (row, value) => rosterDialog.changeChoice("group_ids", row.id, value)
            }
            RowLayout {
                Layout.fillWidth: true
                visible: page.draft("roster", "mode", "groups") === "students"
                OfficeSearchField {
                    id: rosterSearch
                    placeholderText: "ФИО, группа или № зачётки"
                    Keys.onDownPressed: rosterTable.focusFirst()
                }
                CheckBox { id: selectedOnly; text: "Только выбранные" }
            }
            OfficeDataTable {
                id: rosterTable
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: page.draft("roster", "mode", "groups") === "students"
                rows: {
                    const people = page.snapshot.course_students || [];
                    return selectedOnly.checked ? people.filter(row => checkedIds.includes(row.id)) : people;
                }
                columns: [
                    {key: "name", title: "Студент", stretch: true, minimum: 170},
                    {key: "group_number", title: "Группа", numeric: true, center: true, minimum: 65},
                    {key: "record_book_number", title: "№ зачётки", minimum: 95},
                    {key: "specialty", title: "Специальность", minimum: 110}
                ]
                sortField: "name"
                query: rosterSearch.text
                checkable: true
                checkedIds: page.draft("roster", "student_ids", [])
                activeId: rosterDialog.activeStudent
                onSelected: row => rosterDialog.activeStudent = row.id
                onChecked: (row, value) => rosterDialog.changeChoice("student_ids", row.id, value)
            }
            OfficeButton {
                text: "Сохранить состав"
                onClicked: page.app.command("academic." + page.draft("roster", "operation", "add_groups"), page.coursePayload({
                    roster_id: page.selectedRoster().id,
                    name: page.draft("roster", "name", ""),
                    mode: page.draft("roster", "mode", "groups"),
                    group_ids: page.draft("roster", "group_ids", []),
                    student_ids: page.draft("roster", "student_ids", [])
                }))
            }
            OfficeButton {
                text: "Отмена"
                onClicked: rosterDialog.close()
            }
        }
    }
    Dialog {
        id: deleteDialog
        property var target: ({})
        title: "Удалить занятие с сохранением истории"
        modal: true
        width: 500
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            Label {
                Layout.fillWidth: true
                text: deleteDialog.target.name || ""
                wrapMode: Text.Wrap
            }
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Причина удаления"
                text: page.draft("delete", "reason", "")
                onTextEdited: page.put("delete", "reason", text)
            }
            OfficeButton {
                text: "Удалить целое занятие"
                onClicked: page.app.command("academic.delete", page.coursePayload({
                    lesson_id: deleteDialog.target.id,
                    reason: page.draft("delete", "reason", "")
                }))
            }
            OfficeButton {
                text: "Отмена"
                onClicked: deleteDialog.close()
            }
        }
    }
}

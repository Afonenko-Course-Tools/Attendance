import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "TableKeys.js" as TableKeys

Item {
    id: page
    required property var app
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    readonly property int groupColumnWidth: Math.round(65 * interfaceScale)
    readonly property int attendanceColumnWidth: Math.round(178 * interfaceScale)
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    signal historyRequested(var cells, int occurrenceId)
    readonly property var registration: snapshot.registration || ({})
    readonly property var draft: app.drafts.matching || ({})
    readonly property var manual: app.drafts.registration || ({})
    readonly property var lessons: (snapshot.lessons || []).filter(row => (!today.checked || row.is_today) && (!manual.roster_id || row.roster_id === manual.roster_id))
    readonly property bool matchingExpanded: !!draft.uid || !!manual.matchingExpanded
    readonly property bool modalOpen: reasonDialog.visible || cardChange.visible || lessonBox.pickerOpen
    readonly property var matchingStudent: (registration.students || []).find(row => row.id === draft.student_id)
    property string bindingResult: ""
    property string attendanceResult: ""
    Connections {
        target: page.app
        ignoreUnknownSignals: true
        function onContextChanged() {
            page.attendanceResult = "";
        }
        function onCommandSucceeded(name, result) {
            if (name === "registration.bind")
                page.bindingResult = "Карта " + result.uid + " привязана: " + (result.student_name || "");
            if (page.visible && name === "journal.prepare_mark" && result.message)
                page.attendanceResult = result.message;
        }
    }
    onDraftChanged: if (draft.uid) bindingResult = ""
    function showMatching() { app.setDraft("registration", "matchingExpanded", true) }
    function focusSearch() { rosterSearch.forceActiveFocus(); rosterSearch.selectAll() }
    function openCardSearch() { showMatching(); Qt.callLater(() => { search.forceActiveFocus(); search.selectAll() }) }
    function markStudent(studentId) {
        if (studentId)
            app.setDraft("registration", "student_id", studentId)
        if (app.toggleManual) {
            attendanceResult = "";
            app.toggleManual();
        }
    }
    function sortRoster(field) {
        app.setDraft("registration", "descending", manual.sort === field && !manual.descending);
        app.setDraft("registration", "sort", field);
    }
    function rosterHeading(label, field) {
        return label + (manual.sort === field ? (manual.descending ? " ↓" : " ↑") : "");
    }
    function confirmSelection() {
        if (!app.canSaveMatch)
            return
        if (draft.expected_student_id)
            cardChange.open()
        else
            app.command("registration.bind", ({}))
    }
    function revealLesson() {
        app.setDraft("registration", "only_today", false)
        app.setDraft("registration", "roster_id", 0)
    }
    function history() {
        const lesson = (snapshot.lessons || []).find(row => row.id === app.selectedLessonId)
        if (lesson && manual.student_id) historyRequested([{student_id: manual.student_id, topic_id: lesson.topic_id}], lesson.id)
    }
    function syncLesson() {
        const current = app.selectedLessonId
        if (current && !lessons.some(row => row.id === current)) app.selectLesson(0)
        else if (!current && lessons.length) app.selectLesson(lessons[0].id)
    }
    onLessonsChanged: Qt.callLater(syncLesson)
    Component.onCompleted: if (app.selectedLessonId) revealLesson()
    ColumnLayout {
        anchors.fill: parent; spacing: 7
        RowLayout {
            visible: (snapshot.course || {}).kind !== "lecture" && (snapshot.rosters || []).length > 1
            Label { text: "Подгруппа" }
            OfficeComboBox {
                id: rosterBox; Layout.fillWidth: true
                model: [{id: 0, name: "Все подгруппы"}].concat(snapshot.rosters || [])
                textRole: "name"; valueRole: "id"
                currentIndex: Math.max(0, indexOfValue(manual.roster_id || 0))
                onActivated: app.setDraft("registration", "roster_id", currentValue)
            }
        }
        RowLayout {
            Label { text: "Занятие" }
            LessonPickerField {
                id: lessonBox; objectName: "lessonSelector"
                lessons: page.lessons
                value: app.selectedLessonId
                onChosen: id => app.selectLesson(id)
            }
            CheckBox {
                id: today; text: "Только сегодня"
                checked: manual.only_today === undefined ? true : !!manual.only_today
                onToggled: app.setDraft("registration", "only_today", checked)
            }
        }
        Flow {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            Layout.preferredHeight: implicitHeight
            spacing: 4
            OfficeButton { text: "▶ Открыть регистрацию"; enabled: app.canOpen; ToolTip.visible: hovered; ToolTip.text: "Открыть регистрацию · F9"; onClicked: app.command("registration.open", ({})) }
            OfficeButton { text: "■ Завершить"; enabled: app.canClose; ToolTip.visible: hovered; ToolTip.text: "Завершить регистрацию · Shift+F9"; onClicked: app.command("registration.close", ({})) }
            OfficeButton { text: "✓ Ручная отметка"; enabled: app.canManual; ToolTip.visible: hovered; ToolTip.text: "Подтвердить посещение выбранного студента · F2"; onClicked: reasonDialog.open() }
            OfficeButton { text: "▤ Исправить карту"; enabled: !!registration.last_scan && app.canEditMatch; onClicked: { page.showMatching(); app.correctLastCard() } }
        }
        RowLayout {
            visible: !!registration.blocked || (registration.pending_count || 0) > 0
            Label { text: "Не подтверждено предъявлений: " + (registration.pending_count || 0); color: "#b42318"; Layout.fillWidth: true }
            OfficeButton { text: "Повторить запись принятых карт"; enabled: !app.busy; onClicked: app.command("registration.retry", ({})) }
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.Wrap; textFormat: Text.PlainText
            text: !app.selectedLessonId ? (today.checked ? "Сегодня занятий нет. Снимите «Только сегодня», чтобы выбрать другую дату." : "Выберите занятие.") : ((snapshot.course || {}).kind === "lecture" ? "Поток · посещение за занятие" : "Посещение за занятие")
        }
        SplitView {
            Layout.fillWidth: true; Layout.fillHeight: true; orientation: Qt.Horizontal
            ColumnLayout {
                SplitView.fillWidth: true; SplitView.minimumWidth: 420; spacing: 7
                OfficeSearchField {
                    id: rosterSearch
                    Layout.fillWidth: true
                    placeholderText: "ФИО, группа или № зачётки · F3"
                    text: page.manual.roster_query || ""
                    onQueryEdited: query => app.setDraft("registration", "roster_query", query)
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true; border.color: "#cbd5e2"; color: "white"; clip: true
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 1; spacing: 0
                        Rectangle {
                            Layout.fillWidth: true; implicitHeight: Math.round(30 * page.interfaceScale); color: "#edf3fa"
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; spacing: 0
                                OfficeTableHeader { text: page.rosterHeading("ФИО", "name"); Layout.fillWidth: true; Layout.fillHeight: true; TapHandler { onTapped: page.sortRoster("name") } }
                                OfficeTableHeader { text: page.rosterHeading("Группа", "group"); Layout.preferredWidth: page.groupColumnWidth; Layout.fillHeight: true; TapHandler { onTapped: page.sortRoster("group") } }
                                OfficeTableHeader { text: page.rosterHeading("Посещение занятия", "present"); Layout.preferredWidth: page.attendanceColumnWidth; Layout.fillHeight: true; TapHandler { onTapped: page.sortRoster("present") } }
                            }
                        }
                        ListView {
                            id: attendanceList; objectName: "registrationTable"
                            Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                            model: app.registrationRows
                            ScrollBar.vertical: ScrollBar {}
                            delegate: ItemDelegate {
                                required property var record
                                required property int index
                                width: attendanceList.width; height: Math.round((details.checked ? 54 : 30) * page.interfaceScale)
                                padding: 0; highlighted: page.manual.student_id === record.id
                                background: Rectangle {
                                    color: parent.highlighted ? "#dcecff" : (index % 2 ? "#f5f8fc" : "white")
                                    border.color: parent.highlighted ? "#1768b8" : "#e0e6ef"
                                }
                                contentItem: ColumnLayout {
                                    spacing: 0
                                    RowLayout {
                                        Layout.fillWidth: true; Layout.fillHeight: true; spacing: 0
                                        OfficeTableCell { text: record.name; Layout.fillWidth: true; Layout.fillHeight: true; leftPadding: 8 }
                                        OfficeTableCell { text: record.group || "—"; Layout.preferredWidth: page.groupColumnWidth; Layout.fillHeight: true; horizontalAlignment: Text.AlignHCenter }
                                        OfficeTableCell { text: record.attendance_text || (record.present ? "Отметка сохранена" : "Нет отметки"); Layout.preferredWidth: page.attendanceColumnWidth; Layout.fillHeight: true }
                                    }
                                    Label {
                                        visible: details.checked; Layout.fillWidth: true; leftPadding: 8; color: "#62718a"; elide: Text.ElideRight
                                        text: "№ " + (record.record_book_number || "—") + " · " + (record.study_course || "—") + " курс · " + (record.specialty || "—") + (record.profile ? " · " + record.profile : "") + (record.outside ? " · вне состава" : "")
                                        textFormat: Text.PlainText
                                    }
                                }
                                onClicked: {
                                    attendanceList.forceActiveFocus();
                                    app.setDraft("registration", "student_id", record.id);
                                }
                                onDoubleClicked: page.markStudent(record.id)
                            }
                            Keys.onReturnPressed: page.markStudent(null)
                            Keys.onSpacePressed: page.markStudent(null)
                            Keys.onPressed: event => {
                                if (TableKeys.isClearKey(event)) {
                                    if (!event.isAutoRepeat && app.clearManual) {
                                        page.attendanceResult = "";
                                        app.clearManual();
                                    }
                                    event.accepted = true;
                                }
                            }
                        }
                    }
                }
                CheckBox {
                    id: details; text: "Подробнее о студенте"; checked: !!manual.details
                    onToggled: app.setDraft("registration", "details", checked)
                }
                Flow {
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.preferredHeight: implicitHeight
                    spacing: 4
                    OfficeButton { text: "История отметки"; enabled: !!manual.student_id && !!app.selectedLessonId; onClicked: page.history() }
                }
                Label { visible: !!page.attendanceResult; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.attendanceResult }
                Label { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "Отмечено: " + (registration.present_count || 0) + " · по списку: " + (registration.expected_count || 0) + " · непривязанных карт: " + (registration.unknown_count || 0) }
            }
            ColumnLayout {
                SplitView.preferredWidth: 310; SplitView.minimumWidth: 280; spacing: 7
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: lastCard.implicitHeight + 16; radius: 4; color: "#e8f0fb"
                    Label {
                        id: lastCard; anchors.fill: parent; anchors.margins: 8
                        text: registration.last_scan ? "Последнее предъявление: " + (registration.last_scan.name || "Не сопоставлена") + " · " + registration.last_scan.uid : "Последнее предъявление: пока нет"
                        textFormat: Text.PlainText; wrapMode: Text.Wrap; font.bold: true; color: "#234667"
                    }
                }
                OfficeButton { text: "Сопоставление карт"; Layout.fillWidth: true; enabled: app.canEditMatch; onClicked: page.openCardSearch() }
                ColumnLayout {
                    visible: page.matchingExpanded; Layout.fillWidth: true; Layout.fillHeight: true; spacing: 7
                    OfficeComboBox {
                        id: unknownBox; Layout.fillWidth: true; enabled: app.canEditMatch; model: app.unknownCards
                        displayText: page.draft.uid || "Выберите несопоставленную карту"
                        delegate: ItemDelegate {
                            required property var record
                            width: unknownBox.width; text: record.uid
                            onClicked: { app.pickCard(record.uid, false); unknownBox.popup.close() }
                        }
                    }
                    Label { text: draft.uid ? "Карта " + draft.uid + " · " + (draft.owner_name || "не привязана") : "Предъявления сохраняются до сопоставления"; textFormat: Text.PlainText; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    CheckBox { visible: !!draft.expected_student_id; enabled: app.canEditMatch; text: "Исправить прежнего владельца"; checked: !!draft.correction; onToggled: app.setDraft("matching", "correction", checked) }
                    OfficeSearchField {
                        id: search; objectName: "registrationSearch"; enabled: app.canEditMatch
                        Layout.fillWidth: true; placeholderText: "ФИО, группа или № зачётки"; text: draft.query || ""
                        onQueryEdited: query => { app.setDraft("matching", "query", query); app.command("registration.search", {query: query}) }
                        Keys.onDownPressed: candidates.forceActiveFocus()
                    }
                    ListView {
                        id: candidates; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; enabled: app.canEditMatch
                        model: app.matchingStudents; currentIndex: -1; keyNavigationEnabled: true
                        ScrollBar.vertical: ScrollBar {}
                        delegate: ItemDelegate {
                            required property var record
                            width: candidates.width; highlighted: draft.student_id === record.id
                            text: record.full_name + " · " + (record.group_number || "—") + " · " + record.record_book_number
                            onClicked: app.setDraft("matching", "student_id", record.id)
                        }
                        Keys.onReturnPressed: if (currentIndex >= 0) app.setDraft("matching", "student_id", (registration.students || [])[currentIndex].id)
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !!draft.uid
                        wrapMode: Text.Wrap
                        text: page.matchingStudent ? "Выбран студент: " + page.matchingStudent.full_name : "Выберите студента в результатах."
                    }
                    OfficeButton { text: draft.expected_student_id ? "Изменить привязку · Ctrl+Enter" : "Привязать карту · Ctrl+Enter"; Layout.fillWidth: true; enabled: app.canSaveMatch; onClicked: page.confirmSelection() }
                    RowLayout {
                        OfficeButton { text: "Отложить · Esc"; enabled: app.canEditMatch; onClicked: { app.deferMatch(); app.setDraft("registration", "matchingExpanded", false) } }
                        OfficeButton { visible: !!draft.expected_student_id; text: "Снять связь"; enabled: !app.busy && !!draft.correction; onClicked: app.command("registration.unbind", {uid: draft.uid, expected_student_id: draft.expected_student_id, binding_revision: draft.binding_revision}) }
                    }
                }
                OfficeButton { visible: !page.matchingExpanded && (registration.unknown || []).length > 0; text: "Несопоставленных карт: " + (registration.unknown || []).length; Layout.fillWidth: true; onClicked: page.showMatching() }
                Label { visible: !!page.bindingResult; text: page.bindingResult; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Item { visible: !page.matchingExpanded; Layout.fillHeight: true }
            }
        }
        RowLayout {
            visible: !!(snapshot.system || {}).demo
            Label { text: "Имитатор карты" }
            OfficeTextField { id: simulatedUid; Layout.fillWidth: true; placeholderText: "UID в шестнадцатеричном виде" }
            OfficeButton { text: "Предъявить"; enabled: !app.busy && !!registration.open; onClicked: app.command("system.simulate", {uid: simulatedUid.text}) }
        }
    }
    Dialog {
        id: cardChange
        title: "Изменить привязку карты?"
        modal: true
        anchors.centerIn: parent
        width: Math.min(520, page.width - 30)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: ColumnLayout {
            Label {
                Layout.fillWidth: true
                text: "Карта " + (page.draft.uid || "") + ". Текущий владелец: " + (page.draft.owner_name || "")
                    + ". Новый владелец: " + (page.matchingStudent ? page.matchingStudent.full_name : "")
                    + ". Изменится принадлежность сохранённых отметок этой карты."
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
            }
        }
        onAccepted: if (page.app.canSaveMatch) page.app.command("registration.bind", ({}))
    }
    Dialog {
        id: reasonDialog; title: "Ручная отметка"; modal: true; anchors.centerIn: parent; width: Math.min(520, page.width - 30)
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: ColumnLayout {
            Label { text: "Подтвердить посещение выбранного студента"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            OfficeComboBox {
                Layout.fillWidth: true; model: snapshot.manual_reasons || ["был без пропуска", "отработал с другой группой"]
                currentIndex: Math.max(0, model.indexOf(manual.reason || "был без пропуска"))
                onActivated: app.setDraft("registration", "reason", currentText)
            }
        }
        onAccepted: if (app.canManual) app.manualSelected()
    }
}

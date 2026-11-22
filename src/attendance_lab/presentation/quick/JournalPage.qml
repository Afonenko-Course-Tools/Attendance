pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import "TableKeys.js" as TableKeys

Item {
    id: page
    required property var app
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    onInterfaceScaleChanged: table.forceLayout()
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    readonly property bool modalOpen: chooser.visible || historyDialog.visible || exportFile.visible || thresholdDialog.visible
    function exportJournal() {
        exportFile.open();
    }
    function openHistory(cells, occurrenceId) {
        historyDialog.occurrenceId = occurrenceId || 0;
        historyDialog.courseId = app.selectedCourseId;
        historyDialog.cells = cells.map(cell => ({student_id: cell.student_id, topic_id: cell.topic_id}));
        app.command("journal.history", {
            course_id: historyDialog.courseId,
            cells: historyDialog.cells,
            occurrence_id: historyDialog.occurrenceId || null
        });
        historyDialog.open();
    }
    property string attendanceResult: ""
    readonly property var columnKeys: ["name", "group_number", "subgroup", "specialty", "profile"]
    readonly property var metadataWidths: {
        const labels = ["ФИО", "Группа", "Подгруппа", "Специальность", "Профиль"];
        return columnKeys.map((key, index) => {
            const widths = (journal.rows || []).map(row => cellMetrics.advanceWidth(String(row[key] ?? "—"))).sort((a, b) => a - b);
            const fit = widths[Math.ceil(widths.length * 0.9) - 1] || 0;
            return Math.round(Math.max(cellMetrics.advanceWidth(labels[index] + " ↓") + 18 * interfaceScale,
                Math.min((index === 0 ? 420 : 225) * interfaceScale, fit + 18 * interfaceScale)));
        });
    }
    function sortColumn(column) {
        const field = columnKeys[column] || (column === table.columns - 1 ? "missed_count" : "");
        if (!field)
            return;
        app.setDraft("journal", "descending", draft("view_sort", "name") === field && !draft("descending", false));
        app.setDraft("journal", "view_sort", field);
    }
    function columnLabel(column, label) {
        const field = columnKeys[column] || (column === table.columns - 1 ? "missed_count" : "");
        return label + (field === draft("view_sort", "name") ? (draft("descending", false) ? " ↓" : " ↑") : "");
    }
    function applyView() {
        app.journalTable.set_view_sort(draft("view_sort", "name"), draft("descending", false));
        table.forceLayout();
    }
    Component.onCompleted: applyView()
    Connections {
        target: page.app
        ignoreUnknownSignals: true
        function onChanged() {
            page.applyView();
        }
        function onCommandSucceeded(name, result) {
            if (page.visible && (name === "journal.prepare_mark" || name === "journal.confirm_mark") && result.message)
                page.attendanceResult = result.message;
        }
        function onContextChanged() {
            page.attendanceResult = "";
        }
    }
    function focusSearch() {
        search.forceActiveFocus();
        search.selectAll();
    }
    function confirmSelection() {
        mark();
    }
    property var journal: snapshot.journal || ({})
    property var prepared: snapshot.mark_preparation || ({})
    readonly property var filters: draft("filters", journal)
    readonly property string markText: draft("reason", "был без пропуска") === snapshot.excused_reason ? "Отметить отсутствие" : "Поставить посещение"
    function draft(key, fallback) {
        var values = app.drafts.journal || ({});
        return (values[key] === undefined || values[key] === null) ? fallback : values[key];
    }
    function filter(extra) {
        const current = Object.assign({
            query: filters.query || "",
            status: filters.status || "all",
            threshold: filters.threshold || 1
        }, extra);
        app.setDraft("journal", "filters", current);
        app.command("journal.filter", Object.assign({course_id: app.selectedCourseId}, current));
    }
    function mark(toggle) {
        if (toggle ? app.busy || app.selectedCells.length !== 1 : !app.canMarkSelected)
            return;
        attendanceResult = "";
        app.command("journal.prepare_mark", {
            course_id: app.selectedCourseId,
            cells: app.selectedCells,
            toggle: !!toggle,
            reason: draft("reason", "был без пропуска")
        });
    }
    function clearSelected() {
        if (app.busy || !app.selectedCells.length)
            return;
        attendanceResult = "";
        app.command("journal.prepare_mark", {
            course_id: app.selectedCourseId,
            cells: app.selectedCells,
            revoke_only: true
        });
    }
    function selected(sid, tid) {
        return app.selectedCells.some(function (cell) {
            return cell.student_id === sid && cell.topic_id === tid;
        });
    }
    function select(sid, tid, extend) {
        app.setDraft("journal", "cursor", {
            student_id: sid,
            topic_id: tid
        });
        app.selectCell(sid, tid, extend);
    }
    function navigate(dr, dc, extend) {
        var cells = app.selectedCells;
        var current = draft("cursor", cells.length ? cells[cells.length - 1] : ({
                student_id: "",
                topic_id: -1
            }));
        var target = app.journalTable.neighbor(current.student_id, current.topic_id, dr, dc);
        if (target.student_id)
            select(target.student_id, target.topic_id, extend);
    }
    function choice(topic) {
        var key = prepared.token + ":" + topic.topic_id;
        var values = app.drafts.journal || ({});
        if (values[key] !== undefined)
            return values[key];
        var automatic = prepared.automatic_targets || [];
        var found = automatic.find(function (item) {
            return item.topic_id === topic.topic_id;
        });
        return found ? found.occurrence_id : null;
    }
    function confirm() {
        var targets = (prepared.topics || []).map(function (topic) {
            return {
                topic_id: topic.topic_id,
                occurrence_id: page.choice(topic)
            };
        });
        app.command("journal.confirm_mark", {
            course_id: prepared.course_id,
            token: prepared.token,
            targets: targets,
            reason: draft("reason", prepared.reason || "был без пропуска")
        });
    }
    FontMetrics { id: cellMetrics; font: search.font }
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
                text: page.markText
                enabled: page.app.canMarkSelected
                onClicked: page.mark()
                ToolTip.visible: hovered
                ToolTip.text: "Enter / Пробел"
            }
            OfficeComboBox {
                Layout.preferredWidth: Math.round(325 * page.interfaceScale)
                Layout.minimumWidth: 140
                model: page.snapshot.manual_reasons || []
                currentIndex: Math.max(0, model.indexOf(page.draft("reason", "был без пропуска")))
                onActivated: page.app.setDraft("journal", "reason", currentText)
            }
            ToolButton {
                text: "Экспорт"
                enabled: !!page.app.selectedCourseId
                onClicked: page.exportJournal()
            }
            ToolButton {
                text: "История…"
                enabled: !!page.app.selectedCourseId
                onClicked: page.openHistory(page.app.selectedCells)
            }
            ToolButton {
                text: "Столбцы ▾"
                onClicked: viewMenu.popup()
            }
            Item {
                Layout.fillWidth: true
            }
            Label {
                visible: page.app.selectedCells.length > 0
                text: "Выбрано: " + page.app.selectedCells.length
                color: "#62718a"
                rightPadding: 6
            }
        }
        RowLayout {
            spacing: 6
            OfficeSearchField {
                id: search
                placeholderText: "ФИО, группа или № зачётки · F3"
                text: page.filters.query || ""
                onQueryEdited: query => page.filter({query: query})
                onAccepted: {
                    table.forceActiveFocus();
                    page.navigate(0, 0, false);
                }
            }
            OfficeButton {
                text: page.filters.status === "missing"
                      ? ((page.filters.threshold || 1) === 1 ? "Есть пропуски ▾" : "Пропуски от " + page.filters.threshold + " ▾")
                      : "Все студенты ▾"
                visible: !page.journal.observed_only
                onClicked: missedMenu.popup()
                Menu {
                    id: missedMenu
                    MenuItem { text: "Все студенты"; onTriggered: page.filter({status: "all", threshold: 1}) }
                    MenuItem { text: "Есть пропуски"; onTriggered: page.filter({status: "missing", threshold: 1}) }
                    MenuItem { text: "Не меньше…"; onTriggered: thresholdDialog.open() }
                }
            }
        }
        Label {
            visible: !!page.attendanceResult
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: page.attendanceResult
        }
        Label {
            Layout.fillWidth: true
            visible: !!page.journal.observed_only || !!page.journal.incomplete
            text: page.journal.observed_only ? "Состав курса ещё не задан. Показаны студенты с сохранёнными посещениями; пропуски пока не рассчитываются." : "У части прошедших занятий не указан состав. «—» — число пропусков неизвестно; «≥» — известен только минимум. Фильтр учитывает известные пропуски."
            wrapMode: Text.Wrap
            color: "#62718a"
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "white"
            border.color: "#dbe2ed"
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 1
                spacing: 0
                HorizontalHeaderView {
                    id: header
                    Layout.fillWidth: true
                    syncView: table
                    clip: true
                    delegate: Item {
                        id: heading
                        required property int column
                        required property string display
                        implicitWidth: 50
                        implicitHeight: Math.round(38 * page.interfaceScale)
                        OfficeTableHeader {
                            anchors.fill: parent
                            text: page.columnLabel(heading.column, heading.display)
                            TapHandler { onTapped: page.sortColumn(heading.column) }
                        }
                    }
                }
                TableView {
                    id: table
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    focus: true
                    model: page.app.journalTable
                    columnSpacing: 0
                    rowSpacing: 0
                    boundsBehavior: Flickable.StopAtBounds
                    resizableColumns: true
                    columnWidthProvider: function (column) {
                        if (column >= 1 && column < page.columnKeys.length) {
                            const field = page.columnKeys[column];
                            if (!page.draft("column_" + field, column === 1 || page.draft("context", false))
                                || (column === 2 && (page.snapshot.course || {}).kind === "lecture"))
                                return 0;
                        }
                        const explicit = table.explicitColumnWidth(column);
                        if (explicit > 0)
                            return explicit;
                        if (column < page.metadataWidths.length)
                            return page.metadataWidths[column];
                        return Math.round((column === table.columns - 1 ? 111 : 48) * page.interfaceScale);
                    }
                    rowHeightProvider: function (row) {
                        return Math.round(34 * page.interfaceScale);
                    }
                    delegate: Rectangle {
                        id: cell
                        required property int row
                        required property int column
                        required property string display
                        required property string studentId
                        required property int topicId
                        required property bool isPresent
                        required property bool isMissed
                        required property bool isUnresolved
                        required property string cellTitle
                        implicitWidth: 45
                        implicitHeight: Math.round(34 * page.interfaceScale)
                        border.color: "#e8edf4"
                        color: page.selected(studentId, topicId) ? "#deebfa" : row % 2 ? "#f5f8fc" : "white"
                        Label {
                            anchors.fill: parent
                            anchors.margins: 5
                            text: cell.display
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: cell.column === 0 || cell.column === 3 || cell.column === 4 ? Text.AlignLeft : Text.AlignHCenter
                            elide: Text.ElideRight
                            color: "#17243c"
                        }
                        ToolTip.visible: hover.hovered
                        ToolTip.text: cell.cellTitle
                        HoverHandler {
                            id: hover
                        }
                        TapHandler {
                            acceptedButtons: Qt.LeftButton
                            onTapped: {
                                table.forceActiveFocus();
                                if (cell.topicId > 0)
                                    page.select(cell.studentId, cell.topicId, (point.modifiers & Qt.ShiftModifier) !== 0);
                            }
                            onDoubleTapped: {
                                if (cell.topicId > 0) {
                                    page.select(cell.studentId, cell.topicId, false);
                                    page.mark(true);
                                }
                            }
                        }
                    }
                    Keys.onPressed: function (event) {
                        var extend = (event.modifiers & Qt.ShiftModifier) !== 0;
                        if (TableKeys.isClearKey(event)) {
                            if (!event.isAutoRepeat)
                                page.clearSelected();
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
                            page.mark(page.app.selectedCells.length === 1);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Up) {
                            page.navigate(-1, 0, extend);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Down) {
                            page.navigate(1, 0, extend);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Left) {
                            page.navigate(0, -1, extend);
                            event.accepted = true;
                        } else if (event.key === Qt.Key_Right) {
                            page.navigate(0, 1, extend);
                            event.accepted = true;
                        }
                    }
                    ScrollBar.vertical: ScrollBar {}
                    ScrollBar.horizontal: ScrollBar {}
                }
            }
        }
    }
    Menu {
        id: viewMenu
        Repeater {
            model: [
                {field: "group_number", label: "Группа"},
                {field: "subgroup", label: "Подгруппа"},
                {field: "specialty", label: "Специальность"},
                {field: "profile", label: "Профиль"}
            ]
            MenuItem {
                required property var modelData
                text: modelData.label
                visible: modelData.field !== "subgroup" || (page.snapshot.course || {}).kind !== "lecture"
                checkable: true
                checked: page.draft("column_" + modelData.field, modelData.field === "group_number" || page.draft("context", false))
                onTriggered: {
                    page.app.setDraft("journal", "column_" + modelData.field, checked);
                    table.forceLayout();
                }
            }
        }
    }
    Dialog {
        id: thresholdDialog
        title: "Минимальное число пропусков"
        modal: true
        anchors.centerIn: parent
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: {
            threshold.value = page.filters.threshold || 1;
            threshold.forceActiveFocus();
        }
        onAccepted: page.filter({status: "missing", threshold: threshold.value})
        SpinBox {
            id: threshold
            from: 1
            to: 1000
            editable: true
        }
    }
    FileDialog {
        id: exportFile
        title: "Экспорт журнала"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Excel (*.xlsx)", "CSV (*.csv)"]
        onAccepted: page.app.command("journal.export", {
            course_id: page.app.selectedCourseId,
            path: selectedFile.toString(),
            observed_only: !!page.journal.observed_only
        })
    }
    Dialog {
        id: chooser
        title: page.prepared.mode === "revoke" ? "Отмена ручной отметки" : "Ручная отметка — выберите занятие"
        visible: !!page.prepared.token
        modal: true
        closePolicy: Popup.NoAutoClose
        width: Math.min(700, page.width - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            Label {
                text: page.prepared.course_name || ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Repeater {
                model: page.prepared.topics || []
                delegate: ColumnLayout {
                    id: topicChoice
                    required property var modelData
                    Layout.fillWidth: true
                    Label {
                        text: topicChoice.modelData.number + ". " + topicChoice.modelData.title + "\nСтуденты: " + topicChoice.modelData.student_names.join(", ")
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                    LessonPickerField {
                        lessons: topicChoice.modelData.occurrences.map(occurrence => Object.assign(
                            {topic: topicChoice.modelData.title}, occurrence))
                        value: page.choice(topicChoice.modelData)
                        onChosen: id => page.app.setDraft("journal", page.prepared.token + ":" + topicChoice.modelData.topic_id, id)
                    }
                }
            }
            OfficeComboBox {
                visible: page.prepared.mode !== "revoke"
                model: page.snapshot.manual_reasons || []
                currentIndex: Math.max(0, model.indexOf(page.draft("reason", "был без пропуска")))
                onActivated: page.app.setDraft("journal", "reason", currentText)
            }
            OfficeButton {
                text: page.prepared.mode === "revoke" ? "Отменить отметку" : page.markText
                enabled: (page.prepared.topics || []).every(topic => !!page.choice(topic))
                onClicked: page.confirm()
            }
            OfficeButton {
                text: "Отмена"
                onClicked: page.app.command("journal.cancel_mark", {
                    token: page.prepared.token
                })
            }
        }
    }
    Dialog {
        id: historyDialog
        property int courseId: 0
        property int occurrenceId: 0
        property var cells: []
        title: "История ручных отметок"
        modal: true
        width: Math.min(720, page.width - 30)
        height: Math.min(580, page.height - 30)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            enabled: !page.app.busy
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: page.snapshot.manual_history || []
                delegate: Frame {
                    id: batchRow
                    required property var modelData
                    width: ListView.view.width
                    ColumnLayout {
                        width: parent.width
                        Label {
                            text: batchRow.modelData.name + "\n" + batchRow.modelData.students.join(", ")
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                        }
                        Label {
                            text: batchRow.modelData.active ? "Действует" : "Отменена: " + batchRow.modelData.revoked_at
                        }
                        OfficeButton {
                            text: "Отменить эту ручную отметку"
                            enabled: batchRow.modelData.active
                            onClicked: page.app.command("journal.revoke", {
                                course_id: historyDialog.courseId,
                                batch_id: batchRow.modelData.id,
                                cells: historyDialog.cells,
                                occurrence_id: historyDialog.occurrenceId || null
                            })
                        }
                    }
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

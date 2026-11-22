import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

Item {
    id: page
    required property var app
    property var interfaceZoom: null
    readonly property real interfaceScale: interfaceZoom ? interfaceZoom.factor : 1
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    readonly property var grading: snapshot.grading && snapshot.grading.course_id === app.selectedCourseId ? snapshot.grading : ({})
    readonly property var drafts: (app.drafts && app.drafts.grading) || ({})
    readonly property var parameters: draft("parameters", grading.parameters || ({}))
    readonly property var projection: grading.preview && grading.preview.course_id === app.selectedCourseId && grading.preview.revision === draft("revision", 0) ? grading.preview : ({})
    readonly property bool modalOpen: newWorkDialog.visible || pasteDialog.visible || coefficientsDialog.visible || templateDialog.visible || loadTemplateDialog.visible || exportDialog.visible || lessonFilter.pickerOpen || pasteStudent.pickerOpen
    function closeReport() {
        reportDialog.close();
    }
    function openNewWork() {
        newWorkDialog.open();
    }
    function focusSearch() {
        if (activeSection >= 0)
            studentSearch.forceActiveFocus();
        else if (setupButton.visible)
            setupButton.forceActiveFocus();
    }
    property var criterionDefinitions: []
    component HeaderCell: OfficeTableHeader {}
    component TableCell: OfficeTableCell {}

    readonly property var sections: {
        const tabs = [];
        if (grading.reports_enabled)
            tabs.push({index: 0, label: "Доклады"});
        if ((grading.works || []).length)
            tabs.push({index: 1, label: "Контрольные работы"});
        if (tabs.length)
            tabs.push({index: 2, label: "Итоги"});
        return tabs;
    }
    readonly property int activeSection: {
        const requested = draft("section", -1);
        return sections.some(section => section.index === requested) ? requested : (sections[0]?.index ?? -1);
    }
    readonly property var metric: draft("metric", null)
    readonly property var variantOptions: (grading.variants || []).filter(row => row.work_id === metric)
    readonly property var variantStatistics: (projection.variant_statistics || []).filter(
        row => typeof metric !== "number" || row.work_id === metric)
    readonly property var fitting: projection.fit_statistics || ({})
    readonly property var lessonOptions: {
        const lessons = new Map((grading.lessons || []).map(lesson => [lesson.id, lesson]));
        for (const row of grading.reports || [])
            if (row.archived_lesson && !lessons.has(row.archived_lesson.id))
                lessons.set(row.archived_lesson.id, row.archived_lesson);
        return Array.from(lessons.values());
    }
    function matchesStudent(record) {
        if (app.visibleGradeStudentIds !== undefined)
            return app.visibleGradeStudentIds.includes(record.student_id);
        const text = record.full_name.toLowerCase().replace(/ё/g, "е");
        return studentSearch.text.trim().toLowerCase().replace(/ё/g, "е").split(/\s+/)
            .every(term => text.includes(term));
    }
    function sortReports(field) {
        const descending = draft("report_sort", "") === field && !draft("report_descending", false);
        put("report_descending", descending);
        put("report_sort", field);
    }
    FontMetrics { id: headerMetrics; font: studentSearch.font }
    function contentWidth(values) {
        const widths = values.map(value => headerMetrics.advanceWidth(display(value))).sort((a, b) => a - b);
        return widths[Math.ceil(widths.length * 0.9) - 1] || 0;
    }
    readonly property var reportSizing: {
        const labels = {full_name: "Студент", group: "Группа", topic: "Тема доклада", lesson: "Занятие сдачи", grade: "Оценка /10"};
        const rows = grading.reports || [];
        const widths = {};
        for (const field of Object.keys(labels)) {
            widths[field] = Math.max(headerMetrics.advanceWidth(labels[field]) + 35 * interfaceScale,
                Math.min(420 * interfaceScale, contentWidth(rows.map(row => row[field])) + 24 * interfaceScale));
        }
        return widths;
    }
    function customWidth(column) {
        return app.reportColumns === undefined ? draft("report_width_" + column, 0) : app.reportColumns[column] || 0;
    }
    function columnWidth(column, preferred) {
        const custom = customWidth(column);
        return custom ? custom * interfaceScale : (reportSizing[column] || preferred * interfaceScale);
    }
    function resizeReportColumn(column, width) {
        const value = width ? Math.max(50, Math.round(width / interfaceScale)) : 0;
        if (app.resizeReportColumn)
            app.resizeReportColumn(column, value);
        else
            put("report_width_" + column, value);
    }
    component ResizableHeader: HeaderCell {
        id: header
        signal sortRequested()
        signal widthRequested(real width)
        TapHandler { onTapped: header.sortRequested() }
        MouseArea {
            anchors.right: parent.right
            height: parent.height
            width: 7
            cursorShape: Qt.SplitHCursor
            property real startX
            property real startWidth
            onPressed: mouse => {
                startX = mapToItem(page, mouse.x, 0).x;
                startWidth = header.width;
            }
            onPositionChanged: mouse => {
                if (pressed)
                    header.widthRequested(startWidth + mapToItem(page, mouse.x, 0).x - startX);
            }
            onDoubleClicked: header.widthRequested(0)
        }
    }
    component ReportHeader: ResizableHeader {
        required property string field
        required property real preferred
        Layout.preferredWidth: page.columnWidth(field, preferred)
        Layout.fillWidth: !page.customWidth(field)
        Layout.minimumWidth: page.customWidth(field) ? Layout.preferredWidth : 45
        onSortRequested: page.sortReports(field)
        onWidthRequested: width => page.resizeReportColumn(field, width)
    }

    readonly property var writtenSort: draft("work_sort", ({}))
    readonly property var writtenSizing: {
        const rows = grading.scoreRows || [];
        const scale = interfaceScale;
        const widths = {
            full_name: Math.round(Math.max(125 * scale,
                Math.min(420 * scale, contentWidth(rows.map(row => row.full_name)) + 24 * scale)))
        };
        (grading.works || []).forEach((work, index) => {
            const cells = rows.map(row => row.cells[index]).filter(Boolean);
            const scoreWidth = Math.max(headerMetrics.advanceWidth("Балл"), contentWidth(cells.map(cell => cell.text)));
            const variantWidth = Math.max(headerMetrics.advanceWidth("Не указан"), contentWidth(cells.map(cell => cell.variant)));
            widths[work.id] = Math.round(Math.min(420 * scale, Math.max(
                headerMetrics.advanceWidth(work.title + " /" + work.maximum + " ↓") + 24 * scale,
                (scoreWidth + 18 * scale) / 0.42, (variantWidth + 18 * scale) / 0.58)));
        });
        return widths;
    }
    function writtenColumnWidth(field) {
        const manual = draft("work_width:" + field, 0);
        return manual ? Math.round(manual * interfaceScale) : (writtenSizing[field] || Math.round(150 * interfaceScale));
    }
    function resizeWrittenColumn(field, width) {
        const minimum = field === "full_name" ? 62 : 112;
        put("work_width:" + field, width === 0 ? 0 : Math.max(minimum, Math.round(width / interfaceScale)));
    }
    function sortWritten(field) {
        put("work_sort", {field: field, descending: writtenSort.field === field && !writtenSort.descending});
    }
    component WrittenHeader: ResizableHeader {
        required property var field
        required property string label
        width: page.writtenColumnWidth(field)
        text: label + (page.writtenSort.field === field ? (page.writtenSort.descending ? " ↓" : " ↑") : "")
        onSortRequested: page.sortWritten(field)
        onWidthRequested: width => page.resizeWrittenColumn(field, width)
    }
    function selectedReport() {
        if (app.visibleReportIds !== undefined && !app.visibleReportIds.includes(draft("selected_report", "")))
            return null;
        for (const row of grading.reports || [])
            if (row.student_id === draft("selected_report", ""))
                return row;
        return null;
    }
    Rectangle {
        anchors.fill: parent
        color: "white"
        z: -1
    }

    function copy(value) {
        return JSON.parse(JSON.stringify(value));
    }
    function key(course, name) {
        return String(course) + ":" + name;
    }
    function draftAt(course, name, fallback) {
        const value = drafts[key(course, name)];
        return value === undefined ? fallback : value;
    }
    function draft(name, fallback) {
        return draftAt(app.selectedCourseId, name, fallback);
    }
    function putAt(course, name, value) {
        app.setDraft("grading", key(course, name), value);
    }
    function put(name, value) {
        putAt(app.selectedCourseId, name, value);
    }
    function sendAt(course, name, payload) {
        const captured = copy(payload || ({}));
        captured.course_id = course;
        app.command("grading." + name, captured);
    }
    function scoreField(studentId, workId) {
        return "score:" + studentId + ":" + workId;
    }
    function send(name, payload) {
        sendAt(app.selectedCourseId, name, payload);
    }
    function invalidatePreview() {
        put("revision", draft("revision", 0) + 1);
    }
    function setParameter(name, value) {
        invalidatePreview();
        const values = copy(parameters);
        values[name] = value;
        put("parameters", values);
    }
    function setCoefficient(index, name, value) {
        invalidatePreview();
        const values = copy(parameters);
        values.coefficients[index][name] = value;
        put("parameters", values);
    }
    function previewPayload() {
        const selectedSlice = draft("slice", ({}));
        return {
            parameters: copy(parameters),
            revision: draft("revision", 0),
            metric: metric,
            variant: variantOptions.some(row => row.variant === draft("variant", null)) ? draft("variant", null) : null,
            group_id: selectedSlice.group_id || null,
            specialty: selectedSlice.specialty || null
        };
    }
    function preview() {
        send("preview", previewPayload());
    }
    function fit() {
        const payload = previewPayload();
        payload.bounds = [];
        const bounds = draft("bounds", ({}));
        for (const row of grading.parameter_fields || []) {
            const values = bounds[row.key] || row;
            payload.bounds.push({
                key: row.key,
                minimum: values.minimum,
                maximum: values.maximum,
                locked: values.locked || false
            });
        }
        for (const row of parameters.coefficients || [])
            payload.bounds.push({
                key: row.key,
                minimum: row.minimum,
                maximum: row.maximum,
                locked: row.locked || false
            });
        payload.target_mean = draft("target_mean", 7);
        payload.target_deviation = draft("target_deviation", 1.5);
        send("fit", payload);
    }
    function setBound(name, field, value) {
        const bounds = copy(draft("bounds", ({})));
        if (!bounds[name]) {
            for (const row of grading.parameter_fields || [])
                if (row.key === name)
                    bounds[name] = copy(row);
        }
        bounds[name][field] = value;
        put("bounds", bounds);
    }
    function beginReport(row) {
        if (reportDialog.visible) {
            if (reportDialog.visibility === Window.Minimized)
                reportDialog.showNormal();
            reportDialog.requestActivate();
            return;
        }
        reportDialog.courseId = app.selectedCourseId;
        reportDialog.studentName = row.full_name;
        reportDialog.lessonOptions = (grading.lessons || row.lessons || []).slice();
        if (row.archived_lesson && !reportDialog.lessonOptions.some(lesson => lesson.id === row.archived_lesson.id))
            reportDialog.lessonOptions = reportDialog.lessonOptions.concat([row.archived_lesson]);
        const existing = draft("report:" + row.student_id, null);
        if (!existing) {
            put("report_revision:" + row.student_id, draft("report_revision:" + row.student_id, 0) + 1);
            put("report:" + row.student_id, copy(row.card));
        }
        reportDialog.studentId = row.student_id;
        reportDialog.topicResultsVisible = false;
        reportDialog.errorText = "";
        reportDialog.writtenExpanded = !!reportDialog.card.written;
        reportDialog.show();
        reportDialog.requestActivate();
        reportDialog.preview();
    }
    function display(value) {
        return value === null || value === undefined ? "—" : String(value);
    }
    function statistic(value) {
        return typeof value === "number" ? Number(value.toFixed(2)).toLocaleString(Qt.locale("ru_RU")) : "—";
    }
    function summary(stats) {
        if (!stats)
            return "";
        return "N=" + stats.n + "; неполных " + stats.incomplete + "; среднее " + display(stats.mean) + "; σ " + display(stats.deviation);
    }
    function syncProjection() {
        if (!criterionDefinitions.length && grading.criteria)
            criterionDefinitions = copy(grading.criteria).sort((a, b) => Number(a.written) - Number(b.written) || a.index - b.index);
        if (grading.edit_token !== undefined && grading.edit_token !== draft("edit_token", 0)) {
            put("edit_token", grading.edit_token);
            if (grading.edit_revision === draft("revision", 0)) {
                if (grading.edit_kind === "template")
                    invalidatePreview();
                put("parameters", copy(grading.edit_parameters));
            }
        }
    }

    onGradingChanged: Qt.callLater(syncProjection)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 6
        RowLayout {
            Layout.fillWidth: true
            visible: page.activeSection >= 0
            OfficeSearchField {
                id: studentSearch
                placeholderText: "ФИО, группа или № зачётки · F3"
                text: page.draft("search", "")
                onQueryEdited: query => page.put("search", query)
            }
            OfficeButton {
                text: "Экспорт XLSX"
                enabled: !app.busy
                onClicked: {
                    exportDialog.courseId = app.selectedCourseId;
                    exportDialog.open();
                }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: page.activeSection < 0
            visible: page.activeSection < 0 || !page.grading.reports_enabled
            Label {
                visible: page.activeSection < 0
                text: app.selectedCourseId ? "В курсе пока нет оценивания. Включите доклады или добавьте контрольную работу."
                                           : "Выберите курс, чтобы настроить оценивание."
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            OfficeButton {
                id: setupButton
                text: page.activeSection < 0 ? "Настроить оценивание" : "Включить доклады"
                visible: !!app.selectedCourseId
                enabled: !app.busy
                onClicked: {
                    if (page.activeSection < 0)
                        setupMenu.popup();
                    else
                        page.send("enable_reports", {});
                }
                Menu {
                    id: setupMenu
                    MenuItem { text: "Включить доклады"; onTriggered: page.send("enable_reports", {}) }
                    MenuItem { text: "Добавить контрольную работу…"; onTriggered: page.openNewWork() }
                }
            }
            Item { visible: page.activeSection < 0; Layout.fillHeight: true }
        }
        TabBar {
            id: tabs
            objectName: "gradingTabs"
            Layout.fillWidth: true
            visible: page.activeSection >= 0
            currentIndex: page.sections.findIndex(section => section.index === page.activeSection)
            Repeater {
                model: page.sections
                OfficeTabButton {
                    required property var modelData
                    text: modelData.label
                    width: implicitWidth
                    padding: 8
                    leftPadding: 16
                    rightPadding: 16
                    font.bold: checked
                    onClicked: page.put("section", modelData.index)
                }
            }
        }
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: page.activeSection >= 0
            currentIndex: page.activeSection
            ColumnLayout {
                RowLayout {
                    OfficeButton {
                        text: "Оценить доклад"
                        visible: !!page.grading.reports_enabled
                        enabled: page.selectedReport() !== null && !app.busy
                        onClicked: page.beginReport(page.selectedReport())
                    }
                    Label {
                        text: "Дважды нажмите на студента, чтобы открыть карточку из 18 критериев."
                        visible: !!page.grading.reports_enabled
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    OfficeComboBox {
                        id: groupFilter
                        model: [{id: 0, label: "Все группы"}].concat(
                            Array.from(new Map((page.grading.reports || []).filter(row => row.group_id)
                                .map(row => [row.group_id, {id: row.group_id, label: "Группа " + row.group, number: row.group_number}])).values())
                                .sort((a, b) => a.number - b.number))
                        textRole: "label"
                        valueRole: "id"
                        currentIndex: Math.max(0, indexOfValue(page.draft("report_group", 0)))
                        onActivated: page.put("report_group", currentValue)
                    }
                    OfficeComboBox {
                        model: [{id: "", label: "Все статусы"}, {id: "pending", label: "Не оценены"}, {id: "graded", label: "Оценены"}]
                        valueRole: "id"
                        textRole: "label"
                        currentIndex: Math.max(0, indexOfValue(page.draft("report_status", "")))
                        onActivated: page.put("report_status", currentValue)
                    }
                    LessonPickerField {
                        id: lessonFilter
                        Layout.fillWidth: true
                        allowAll: true
                        lessons: page.lessonOptions
                        value: page.draft("report_lesson", 0)
                        onChosen: id => page.put("report_lesson", id)
                    }
                    OfficeButton {
                        text: "Сбросить"
                        onClicked: {
                            page.put("search", "");
                            page.put("report_group", "");
                            page.put("report_status", "");
                            page.put("report_lesson", 0);
                        }
                    }
                }
                Label {
                    visible: page.app.visibleReportIds !== undefined
                    text: "Показано " + (page.app.visibleReportIds || []).length + " из " + (page.grading.reports || []).length
                }
                ListView {
                    id: reports
                    objectName: "gradingReports"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 0
                    flickableDirection: Flickable.HorizontalAndVerticalFlick
                    contentWidth: Math.max(width, 34 + page.columnWidth("full_name", 270) + page.columnWidth("group", 66) + page.columnWidth("topic", 220) + page.columnWidth("lesson", 210) + page.columnWidth("grade", 90))
                    ScrollBar.horizontal: ScrollBar {}
                    model: app.gradingReportRows
                    headerPositioning: ListView.OverlayHeader
                    header: RowLayout {
                        width: reports.contentWidth
                        height: Math.round(36 * page.interfaceScale)
                        spacing: 0
                        z: 2
                        HeaderCell {
                            text: ""
                            Layout.preferredWidth: 34
                            Layout.fillHeight: true
                        }
                        ReportHeader {
                            field: "full_name"
                            preferred: 270
                            text: "Студент" + (page.draft("report_sort", "") === "full_name" ? (page.draft("report_descending", false) ? " ↓" : " ↑") : "")
                            Layout.fillHeight: true
                        }
                        ReportHeader {
                            field: "group"
                            preferred: 66
                            text: "Группа" + (page.draft("report_sort", "") === "group" ? (page.draft("report_descending", false) ? " ↓" : " ↑") : "")
                            Layout.fillHeight: true
                        }
                        ReportHeader {
                            field: "topic"
                            preferred: 220
                            text: "Тема доклада" + (page.draft("report_sort", "") === "topic" ? (page.draft("report_descending", false) ? " ↓" : " ↑") : "")
                            Layout.fillHeight: true
                        }
                        ReportHeader {
                            field: "lesson"
                            preferred: 210
                            text: "Занятие сдачи" + (page.draft("report_sort", "") === "lesson" ? (page.draft("report_descending", false) ? " ↓" : " ↑") : "")
                            Layout.fillHeight: true
                        }
                        ReportHeader {
                            field: "grade"
                            preferred: 90
                            text: "Оценка /10" + (page.draft("report_sort", "") === "grade" ? (page.draft("report_descending", false) ? " ↓" : " ↑") : "")
                            Layout.fillHeight: true
                        }
                    }
                    delegate: ItemDelegate {
                        id: reportRow
                        required property var record
                        required property int index
                        width: reports.contentWidth
                        visible: page.app.visibleReportIds !== undefined || page.matchesStudent(record)
                        height: visible ? Math.round(30 * page.interfaceScale) : 0
                        padding: 0
                        highlighted: page.draft("selected_report", "") === record.student_id
                        background: Rectangle {
                            color: reportRow.highlighted ? "#deebfa" : reportRow.index % 2 ? "#f5f8fc" : "white"
                        }
                        contentItem: RowLayout {
                            spacing: 0
                            TableCell {
                                text: reportRow.index + 1
                                Layout.preferredWidth: 34
                                Layout.fillHeight: true
                                horizontalAlignment: Text.AlignHCenter
                            }
                            TableCell {
                                text: reportRow.record.full_name
                                Layout.preferredWidth: page.columnWidth("full_name", 270)
                                Layout.minimumWidth: page.customWidth("full_name") ? Layout.preferredWidth : 45
                                Layout.fillWidth: !page.customWidth("full_name")
                                Layout.fillHeight: true
                            }
                            TableCell {
                                text: reportRow.record.group
                                horizontalAlignment: Text.AlignHCenter
                                Layout.preferredWidth: page.columnWidth("group", 66)
                                Layout.minimumWidth: page.customWidth("group") ? Layout.preferredWidth : 45
                                Layout.fillWidth: !page.customWidth("group")
                                Layout.fillHeight: true
                            }
                            TableCell {
                                text: reportRow.record.topic || "Не оценён"
                                Layout.preferredWidth: page.columnWidth("topic", 220)
                                Layout.minimumWidth: page.customWidth("topic") ? Layout.preferredWidth : 45
                                Layout.fillWidth: !page.customWidth("topic")
                                Layout.fillHeight: true
                            }
                            TableCell {
                                text: reportRow.record.lesson
                                Layout.preferredWidth: page.columnWidth("lesson", 210)
                                Layout.minimumWidth: page.customWidth("lesson") ? Layout.preferredWidth : 45
                                Layout.fillWidth: !page.customWidth("lesson")
                                Layout.fillHeight: true
                            }
                            TableCell {
                                text: page.display(reportRow.record.grade)
                                Layout.preferredWidth: page.columnWidth("grade", 90)
                                Layout.minimumWidth: page.customWidth("grade") ? Layout.preferredWidth : 45
                                Layout.fillWidth: !page.customWidth("grade")
                                Layout.fillHeight: true
                            }
                        }
                        enabled: !!page.grading.reports_enabled && !app.busy
                        onClicked: page.put("selected_report", record.student_id)
                        onDoubleClicked: page.beginReport(record)
                        Keys.onReturnPressed: page.beginReport(record)
                        Accessible.name: record.full_name + ", " + record.topic + ", " + page.display(record.grade)
                    }
                    ScrollBar.vertical: ScrollBar {}
                }
            }
            ColumnLayout {
                RowLayout {
                    OfficeButton {
                        text: "Добавить контрольную"
                        enabled: !app.busy
                        onClicked: newWorkDialog.open()
                    }
                    OfficeButton {
                        text: "Вставить прямоугольник · Ctrl+V"
                        enabled: !app.busy && (page.grading.works || []).length > 0
                        onClicked: pasteDialog.open()
                    }
                    Label {
                        text: "Enter сохраняет балл и вариант. Delete или Backspace очищает выбранный результат."
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                    }
                }
                ListView {
                    id: scoreRows
                    objectName: "gradingScores"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 0
                    model: app.gradingWorkRows
                    readonly property int indexWidth: Math.round(34 * page.interfaceScale)
                    contentWidth: Math.max(width, indexWidth + page.writtenColumnWidth("full_name")
                        + (page.grading.works || []).reduce((total, work) => total + page.writtenColumnWidth(work.id), 0))
                    flickableDirection: Flickable.HorizontalAndVerticalFlick
                    headerPositioning: ListView.OverlayHeader
                    header: Row {
                        width: scoreRows.contentWidth
                        height: Math.round(36 * page.interfaceScale)
                        z: 2
                        HeaderCell {
                            text: ""
                            width: scoreRows.indexWidth
                            height: parent.height
                        }
                        WrittenHeader {
                            field: "full_name"
                            label: "Студент"
                            height: parent.height
                        }
                        Repeater {
                            model: page.grading.works || []
                            WrittenHeader {
                                required property var modelData
                                field: modelData.id
                                label: modelData.title + " /" + modelData.maximum
                                height: parent.height
                            }
                        }
                    }
                    delegate: Row {
                        id: scoreRow
                        required property var record
                        required property int index
                        width: scoreRows.contentWidth
                        visible: page.matchesStudent(record)
                        height: visible ? Math.round(30 * page.interfaceScale) : 0
                        TableCell {
                            text: scoreRow.index + 1
                            width: scoreRows.indexWidth
                            height: parent.height
                            horizontalAlignment: Text.AlignHCenter
                        }
                        TableCell {
                            text: scoreRow.record.full_name
                            width: page.writtenColumnWidth("full_name")
                            height: parent.height
                        }
                        Repeater {
                            model: scoreRow.record.cells
                            WrittenScoreEntry {
                                required property var modelData
                                readonly property string fieldKey: page.scoreField(modelData.student_id, modelData.work_id)
                                width: page.writtenColumnWidth(modelData.work_id)
                                height: scoreRow.height
                                enabled: !app.busy
                                alternate: scoreRow.index % 2 !== 0
                                score: page.draft(fieldKey, null) ?? modelData.text ?? ""
                                variant: page.draft(fieldKey + ":variant", null) ?? modelData.variant ?? ""
                                legacy: !!modelData.legacy
                                label: scoreRow.record.full_name + ": " + modelData.title
                                onScoreEdited: value => page.put(fieldKey, value)
                                onVariantEdited: value => page.put(fieldKey + ":variant", value)
                                onSubmitted: (score, variant) => page.send("score", {
                                    student_id: modelData.student_id,
                                    work_id: modelData.work_id,
                                    value: score,
                                    variant: variant
                                })
                                onClearRequested: page.send("score", {
                                    student_id: modelData.student_id,
                                    work_id: modelData.work_id,
                                    value: null
                                })
                            }
                        }
                    }
                    ScrollBar.horizontal: ScrollBar {}
                    ScrollBar.vertical: ScrollBar {}
                }
                Label {
                    visible: !(page.grading.works || []).length
                    text: "Контрольных пока нет. Добавьте название и исходную шкалу."
                }
            }
            ColumnLayout {
                RowLayout {
                    OfficeButton {
                        text: settings.visible ? "Скрыть настройки" : "Настроить расчёт"
                        onClicked: settings.visible = !settings.visible
                    }
                    OfficeButton {
                        text: "Предпросмотр"
                        enabled: !app.busy
                        onClicked: page.preview()
                    }
                    Label {
                        text: "Применённый вариант v" + (page.grading.parameters || {}).version
                        Layout.fillWidth: true
                    }
                }
                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: parent.width
                        spacing: 12
                        ColumnLayout {
                            id: settings
                            visible: false
                            Layout.fillWidth: true
                            Repeater {
                                model: page.grading.parameter_fields || []
                                RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Label {
                                        text: modelData.title
                                        Layout.preferredWidth: 230
                                        wrapMode: Text.Wrap
                                    }
                                    OfficeTextField {
                                        Layout.preferredWidth: 90
                                        text: page.display(page.parameters[modelData.key])
                                        onTextEdited: page.setParameter(modelData.key, text)
                                        onAccepted: page.preview()
                                    }
                                    Label {
                                        text: "Пределы"
                                    }
                                    OfficeTextField {
                                        Layout.preferredWidth: 70
                                        text: page.display((page.draft("bounds", {})[modelData.key] || modelData).minimum)
                                        onTextEdited: page.setBound(modelData.key, "minimum", text)
                                    }
                                    OfficeTextField {
                                        Layout.preferredWidth: 70
                                        text: page.display((page.draft("bounds", {})[modelData.key] || modelData).maximum)
                                        onTextEdited: page.setBound(modelData.key, "maximum", text)
                                    }
                                    CheckBox {
                                        text: "Закрепить"
                                        checked: (page.draft("bounds", {})[modelData.key] || {}).locked || false
                                        onToggled: page.setBound(modelData.key, "locked", checked)
                                    }
                                }
                            }
                            OfficeButton {
                                text: "Веса критериев, работ и уровни…"
                                onClicked: coefficientsDialog.open()
                            }
                            RowLayout {
                                Label {
                                    text: "Целевая средняя"
                                }
                                OfficeTextField {
                                    Layout.preferredWidth: 80
                                    text: String(page.draft("target_mean", 7))
                                    onTextEdited: page.put("target_mean", text)
                                }
                                Label {
                                    text: "Целевой разброс"
                                }
                                OfficeTextField {
                                    Layout.preferredWidth: 80
                                    text: String(page.draft("target_deviation", 1.5))
                                    onTextEdited: page.put("target_deviation", text)
                                }
                                OfficeButton {
                                    text: "Подобрать в пределах"
                                    enabled: !app.busy
                                    onClicked: page.fit()
                                }
                            }
                            RowLayout {
                                OfficeButton {
                                    text: "Применить ко всему курсу"
                                    enabled: !app.busy
                                    onClicked: page.send("apply", {
                                        parameters: page.parameters,
                                        parameter_version: page.parameters.version
                                    })
                                }
                                OfficeButton {
                                    text: "Сохранить вариант"
                                    enabled: !app.busy
                                    onClicked: templateDialog.open()
                                }
                                OfficeButton {
                                    text: "Загрузить вариант"
                                    enabled: !app.busy
                                    onClicked: loadTemplateDialog.open()
                                }
                                OfficeButton {
                                    text: "Вернуть применённый"
                                    onClicked: {
                                        page.invalidatePreview();
                                        page.put("parameters", page.copy(page.grading.parameters));
                                        page.preview();
                                    }
                                }
                            }
                        }
                        RowLayout {
                            Label {
                                text: "Распределение"
                            }
                            OfficeComboBox {
                                model: page.grading.metrics || []
                                textRole: "label"
                                valueRole: "value"
                                currentIndex: Math.max(0, indexOfValue(page.metric))
                                onActivated: {
                                    page.invalidatePreview();
                                    page.put("metric", currentValue);
                                    page.put("variant", null);
                                    page.preview();
                                }
                            }
                            OfficeComboBox {
                                model: page.grading.slices || []
                                textRole: "label"
                                onActivated: {
                                    page.invalidatePreview();
                                    page.put("slice", model[currentIndex]);
                                    page.preview();
                                }
                            }
                            OfficeComboBox {
                                visible: page.variantOptions.length > 0
                                model: [{variant: null, label: "Все варианты"}].concat(page.variantOptions)
                                textRole: "label"
                                valueRole: "variant"
                                currentIndex: Math.max(0, indexOfValue(page.draft("variant", null)))
                                onActivated: {
                                    page.invalidatePreview();
                                    page.put("variant", currentValue);
                                    page.preview();
                                }
                            }
                        }
                        Label {
                            text: "Применено: " + page.summary(page.projection.baseline_statistics)
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                        }
                        Label {
                            text: "Предпросмотр: " + page.summary(page.projection.statistics)
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                        }
                        Label {
                            text: (page.projection.statistics || {}).normality || ""
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Repeater {
                                model: page.projection.histogram || []
                                ColumnLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Row {
                                        Layout.preferredHeight: 90
                                        Layout.alignment: Qt.AlignHCenter
                                        spacing: 2
                                        Rectangle {
                                            width: 12
                                            height: 90 * modelData.baseline / (page.projection.histogram_max || 1)
                                            color: "#245a91"
                                            anchors.bottom: parent.bottom
                                        }
                                        Rectangle {
                                            width: 12
                                            height: 90 * modelData.candidate / (page.projection.histogram_max || 1)
                                            color: "#b36700"
                                            anchors.bottom: parent.bottom
                                        }
                                    }
                                    Label {
                                        text: String(modelData.baseline) + " / " + modelData.candidate
                                        Layout.alignment: Qt.AlignHCenter
                                    }
                                    Label {
                                        text: String(modelData.mark)
                                        font.bold: true
                                        Layout.alignment: Qt.AlignHCenter
                                    }
                                }
                            }
                        }
                        Label {
                            text: "Распределение по отметкам: применено / предпросмотр"
                            wrapMode: Text.Wrap
                        }
                        Label {
                            visible: page.variantStatistics.length > 0
                            text: "Статистика контрольных по вариантам"
                            font.bold: true
                            Layout.fillWidth: true
                        }
                        Label {
                            visible: page.fitting.n !== undefined && (page.grading.works || []).length > 0
                            text: page.fitting.balanced
                                ? "Подбор уравновешивает варианты. Полных результатов: " + page.fitting.n
                                : "Полных результатов для подбора: " + page.fitting.n
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                        }
                        OfficeDataTable {
                            visible: page.variantStatistics.length > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: Math.min(290, Math.round((1 + rows.length) * 36 * page.interfaceScale) + 8)
                            rows: page.variantStatistics.map(row => Object.assign({}, row, {
                                id: String(row.work_id) + ":" + String(row.variant ?? ""),
                                variant_label: row.variant ?? "Не указан",
                                mean_label: page.statistic(row.mean),
                                deviation_label: page.statistic(row.deviation),
                                fit_label: row.fit_eligible ? "Да" : "Нет"
                            }))
                            columns: [
                                {key: "work_title", title: "Контрольная", stretch: true, minimum: 140},
                                {key: "variant_label", title: "Вариант", center: true},
                                {key: "n", title: "Оценок", numeric: true, center: true},
                                {key: "mean_label", sortKey: "mean", title: "Средний балл /10", numeric: true, center: true},
                                {key: "deviation_label", sortKey: "deviation", title: "Разброс /10", numeric: true, center: true},
                                {key: "fit_n", title: "Полных в курсе", numeric: true, center: true},
                                {key: "fit_label", title: "В подборе", center: true}
                            ]
                        }
                        ListView {
                            id: changes
                            objectName: "gradingChanges"
                            Layout.fillWidth: true
                            Layout.preferredHeight: 320
                            clip: true
                            model: page.projection.changes ? app.gradingChangeRows : null
                            headerPositioning: ListView.OverlayHeader
                            header: RowLayout {
                                width: changes.width
                                height: Math.round(36 * page.interfaceScale)
                                spacing: 0
                                z: 2
                                HeaderCell {
                                    text: "Студент"
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 300
                                    Layout.fillHeight: true
                                }
                                HeaderCell {
                                    text: "Применено"
                                    Layout.preferredWidth: 105
                                    Layout.fillHeight: true
                                }
                                HeaderCell {
                                    text: "Предпросмотр"
                                    Layout.preferredWidth: 115
                                    Layout.fillHeight: true
                                }
                                HeaderCell {
                                    text: "Готовность"
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 240
                                    Layout.fillHeight: true
                                }
                            }
                            delegate: ItemDelegate {
                                id: changeRow
                                required property var record
                                required property int index
                                width: changes.width
                                padding: 0
                                background: Rectangle {
                                    color: changeRow.index % 2 ? "#f5f8fc" : "white"
                                }
                                contentItem: RowLayout {
                                    spacing: 0
                                    TableCell {
                                        text: changeRow.record.full_name
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 300
                                        Layout.fillHeight: true
                                    }
                                    TableCell {
                                        text: page.display(changeRow.record.baseline)
                                        Layout.preferredWidth: 105
                                        Layout.fillHeight: true
                                    }
                                    TableCell {
                                        text: page.display(changeRow.record.candidate)
                                        Layout.preferredWidth: 115
                                        Layout.fillHeight: true
                                    }
                                    TableCell {
                                        text: changeRow.record.readiness
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 240
                                        Layout.fillHeight: true
                                    }
                                }
                                visible: page.matchesStudent(record)
                                height: visible ? Math.round(30 * page.interfaceScale) : 0
                            }
                            ScrollBar.vertical: ScrollBar {}
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: app
        ignoreUnknownSignals: true
        function onCommandFailed(name, message) {
            if (name === "grading.save_report" && reportDialog.visible)
                reportDialog.errorText = message;
        }
        function onCommandSucceeded(name, result) {
            if (name === "grading.save_report" && result.course_id === reportDialog.courseId && result.student_id === reportDialog.studentId) {
                page.putAt(reportDialog.courseId, "report:" + reportDialog.studentId, null);
                reportDialog.close();
            } else if (name === "grading.add_work" && result.course_id === app.selectedCourseId) {
                page.put("section", 1);
                newWorkDialog.close();
            } else if (name === "grading.enable_reports" && result.course_id === app.selectedCourseId) {
                page.put("section", 0);
            } else if (name === "grading.score" && result.student_id && result.work_id) {
                const field = page.scoreField(result.student_id, result.work_id);
                page.putAt(result.course_id, field, null);
                page.putAt(result.course_id, field + ":variant", null);
            } else if (name === "grading.paste_scores" && result.course_id === app.selectedCourseId) {
                pasteDialog.close();
            } else if (name === "grading.save_template" && result.course_id === app.selectedCourseId) {
                templateDialog.close();
            } else if (name === "grading.load_template") {
                loadTemplateDialog.close();
            } else if (name === "grading.apply" && result.course_id === app.selectedCourseId) {
                const values = page.copy(page.parameters);
                values.version = result.parameter_version;
                page.put("parameters", values);
            }
        }
    }
    ApplicationWindow {
        id: reportDialog
        objectName: "gradingReportDialog"
        readonly property real interfaceScale: page.interfaceScale
        font.pointSize: 12 * interfaceScale
        property int courseId: 0
        property string studentId: ""
        property string studentName: ""
        property var lessonOptions: []
        property bool topicResultsVisible: false
        property bool oralExpanded: true
        property bool writtenExpanded: false
        property string errorText: ""
        readonly property var card: page.draftAt(courseId, "report:" + studentId, ({}))
        readonly property int revision: page.draftAt(courseId, "report_revision:" + studentId, 0)
        readonly property var previewResult: page.grading.report_preview && page.grading.report_preview.course_id === courseId && page.grading.report_preview.student_id === studentId && page.grading.report_preview.revision === revision ? page.grading.report_preview : ({})
        readonly property bool topicMatchesCurrent: page.grading.topic_student_id === studentId && page.grading.topic_revision === revision && page.grading.query === card.topic
        title: "Доклад — " + studentName
        transientParent: null
        flags: Qt.Window
        visible: false
        width: Math.min(Screen.desktopAvailableWidth, 840)
        height: Math.min(Screen.desktopAvailableHeight, 800)
        minimumWidth: 340
        minimumHeight: 320
        color: "#f3f6fa"
        function changedDraft() {
            errorText = "";
            page.putAt(courseId, "report_revision:" + studentId, revision + 1);
        }
        function setField(name, value) {
            changedDraft();
            const values = page.copy(card);
            values[name] = value;
            page.putAt(courseId, "report:" + studentId, values);
        }
        function setLevel(index, value) {
            changedDraft();
            const values = page.copy(card);
            values.levels[index] = value;
            page.putAt(courseId, "report:" + studentId, values);
            preview();
        }
        function preview() {
            page.sendAt(courseId, "report_preview", {
                card: card,
                revision: revision
            });
        }
        function save() {
            errorText = "";
            page.sendAt(courseId, "save_report", {
                card: card
            });
        }
        function reveal(item) {
            const viewport = reportScroll.contentItem;
            const top = item.mapToItem(reportFields, 0, 0).y;
            if (top < viewport.contentY)
                viewport.contentY = top;
            else if (top + item.height > viewport.contentY + viewport.height)
                viewport.contentY = top + item.height - viewport.height;
        }
        function focusCriterion(from, direction) {
            let target = page.criterionDefinitions.findIndex(row => row.index === from) + direction;
            while (target >= 0 && target < rubricRepeater.count) {
                const item = rubricRepeater.itemAt(target);
                if (item.enabled && (item.modelData.written ? writtenExpanded : oralExpanded)) {
                    item.forceActiveFocus();
                    return;
                }
                target += direction;
            }
        }
        ZoomShortcuts { zoom: page.interfaceZoom }
        Shortcut {
            sequence: "Escape"
            enabled: !lessonChoice.pickerOpen
            onActivated: reportDialog.close()
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 12
            ScrollView {
                id: reportScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    id: reportFields
                    width: reportScroll.availableWidth
                    Label {
                        text: "Тема доклада"
                    }
                    OfficeTextField {
                        id: topicEditor
                        Layout.fillWidth: true
                        placeholderText: "Название, раздел или номер · можно свою тему"
                        text: reportDialog.card.topic || ""
                        onActiveFocusChanged: if (activeFocus) reportDialog.reveal(topicEditor)
                        onTextEdited: {
                            reportDialog.setField("topic", text);
                            reportDialog.topicResultsVisible = true;
                            page.sendAt(reportDialog.courseId, "topic_search", {
                                query: text,
                                student_id: reportDialog.studentId,
                                revision: reportDialog.revision
                            });
                        }
                    }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(100, reportDialog.height / 5)
                        visible: reportDialog.topicResultsVisible && reportDialog.topicMatchesCurrent && (page.grading.topic_matches || []).length > 0
                        ListView {
                            model: page.grading.topic_matches || []
                            delegate: ItemDelegate {
                                required property var modelData
                                width: ListView.view.width
                                text: modelData.title + (modelData.section ? " · " + modelData.section : "")
                                onClicked: {
                                    reportDialog.setField("topic", modelData.title);
                                    reportDialog.topicResultsVisible = false;
                                    topicEditor.forceActiveFocus();
                                }
                            }
                        }
                    }
                    LessonPickerField {
                        id: lessonChoice
                        Layout.fillWidth: true
                        lessons: reportDialog.lessonOptions
                        value: reportDialog.card.lesson_id
                        placeholder: "Выберите занятие сдачи"
                        onActiveFocusChanged: if (activeFocus) reportDialog.reveal(lessonChoice)
                        onChosen: id => reportDialog.setField("lesson_id", id)
                    }
                    CheckBox {
                        id: writtenCheck
                        text: "Реферат представлен"
                        checked: !!reportDialog.card.written
                        onActiveFocusChanged: if (activeFocus) reportDialog.reveal(writtenCheck)
                        onToggled: {
                            reportDialog.setField("written", checked);
                            reportDialog.writtenExpanded = checked;
                            reportDialog.preview();
                        }
                    }
                    Repeater {
                        id: rubricRepeater
                        model: page.criterionDefinitions
                        ColumnLayout {
                            required property var modelData
                            readonly property int criterionIndex: modelData.index
                            readonly property int criterionSteps: modelData.steps
                            objectName: "rubricCriterion" + criterionIndex
                            activeFocusOnTab: true
                            onActiveFocusChanged: if (activeFocus && rubricButtons.itemAt((reportDialog.card.levels || [])[criterionIndex]))
                                rubricButtons.itemAt(reportDialog.card.levels[criterionIndex]).forceActiveFocus()
                            Layout.fillWidth: true
                            enabled: !modelData.written || !!reportDialog.card.written
                            OfficeButton {
                                visible: criterionIndex === 13 || criterionIndex === 0
                                Layout.fillWidth: true
                                text: (modelData.written ? (reportDialog.writtenExpanded ? "▾ " : "▸ ") : (reportDialog.oralExpanded ? "▾ " : "▸ ")) + (modelData.written ? "Письменный реферат" : "Устное выступление")
                                onClicked: if (modelData.written)
                                    reportDialog.writtenExpanded = !reportDialog.writtenExpanded
                                else
                                    reportDialog.oralExpanded = !reportDialog.oralExpanded
                            }
                            Label {
                                visible: modelData.written ? reportDialog.writtenExpanded : reportDialog.oralExpanded
                                text: modelData.title
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            GridLayout {
                                visible: modelData.written ? reportDialog.writtenExpanded : reportDialog.oralExpanded
                                Layout.fillWidth: true
                                readonly property real availableWidth: reportDialog.width / reportDialog.interfaceScale
                                columns: availableWidth >= 630 ? criterionSteps : availableWidth >= 380 ? 3 : 2
                                Repeater {
                                    id: rubricButtons
                                    model: modelData.labels
                                    OfficeButton {
                                        id: levelButton
                                        required property string modelData
                                        required property int index
                                        text: modelData
                                        checkable: true
                                        font.bold: checked
                                        implicitHeight: Math.round(48 * reportDialog.interfaceScale)
                                        contentItem: Text {
                                            text: parent.text
                                            font: parent.font
                                            color: !parent.enabled ? "#7b8798" : parent.checked ? "white" : ["#981b1b", "#9d331d", "#234667", "#2b622b", "#166044"][criterionSteps === 3 ? index * 2 : index]
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                            wrapMode: Text.Wrap
                                        }
                                        background: Rectangle {
                                            radius: 4
                                            color: !parent.enabled ? "#f1f3f6" : parent.checked ? "#245a91" : ["#fbe8e6", "#fff0ea", "white", "#edf5eb", "#e1f1eb"][criterionSteps === 3 ? index * 2 : index]
                                            border.color: !parent.enabled ? "#cbd5e2" : parent.checked ? "#245a91" : ["#981b1b", "#9d331d", "#cbd5e2", "#2b622b", "#166044"][criterionSteps === 3 ? index * 2 : index]
                                        }
                                        checked: (reportDialog.card.levels || [])[criterionIndex] === index
                                        Layout.fillWidth: true
                                        Layout.preferredWidth: 0
                                        onActiveFocusChanged: if (activeFocus) reportDialog.reveal(levelButton)
                                        onClicked: reportDialog.setLevel(criterionIndex, index)
                                        Keys.onLeftPressed: reportDialog.setLevel(criterionIndex, Math.max(0, reportDialog.card.levels[criterionIndex] - 1))
                                        Keys.onRightPressed: reportDialog.setLevel(criterionIndex, Math.min(criterionSteps - 1, reportDialog.card.levels[criterionIndex] + 1))
                                        Keys.onUpPressed: reportDialog.focusCriterion(criterionIndex, -1)
                                        Keys.onDownPressed: reportDialog.focusCriterion(criterionIndex, 1)
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Label {
                text: reportDialog.previewResult.grade !== undefined ? "Предварительная оценка: " + reportDialog.previewResult.grade + " /10" : ""
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
            Label {
                visible: !!reportDialog.errorText
                text: reportDialog.errorText
                textFormat: Text.PlainText
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: "#991b1b"
            }
            Flow {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                Layout.preferredHeight: implicitHeight
                spacing: 6
                OfficeButton {
                    text: "Сохранить"
                    enabled: !app.busy
                    onClicked: reportDialog.save()
                }
                OfficeButton {
                    text: "Закрыть"
                    ToolTip.visible: hovered
                    ToolTip.text: "Черновик сохранится"
                    onClicked: reportDialog.close()
                }
                OfficeButton {
                    text: "Отбросить черновик"
                    onClicked: {
                        page.putAt(reportDialog.courseId, "report:" + reportDialog.studentId, null);
                        reportDialog.close();
                    }
                }
            }
        }
    }
    Dialog {
        id: newWorkDialog
        title: "Новая контрольная работа"
        modal: true
        width: Math.min(page.width - 30, 500)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            OfficeTextField {
                placeholderText: "Название"
                text: page.draft("work_title", "")
                onTextEdited: page.put("work_title", text)
            }
            OfficeTextField {
                placeholderText: "Максимум баллов"
                text: page.draft("work_maximum", "100")
                onTextEdited: page.put("work_maximum", text)
            }
            OfficeButton {
                text: "Добавить"
                enabled: !app.busy
                onClicked: page.send("add_work", {
                    title: page.draft("work_title", ""),
                    maximum: page.draft("work_maximum", "100")
                })
            }
        }
    }
    Dialog {
        id: pasteDialog
        property bool pasteOnOpen: false
        property var students: []
        property var studentId: ""
        property var works: []
        onAboutToShow: {
            const byId = new Map((page.grading.reports || []).map(row => [row.student_id, row]));
            students = (page.app.visibleGradeStudentIds ?? page.grading.student_ids ?? [])
                .filter(id => byId.has(id)).map(id => byId.get(id));
            if (!students.some(row => row.student_id === studentId))
                studentId = students.length ? students[0].student_id : "";
            works = (page.grading.works || []).slice();
        }
        onOpened: if (pasteOnOpen) {
            pasteInput.forceActiveFocus();
            pasteInput.selectAll();
            pasteInput.paste();
            pasteOnOpen = false;
        }
        title: "Вставка прямоугольника исходных баллов"
        modal: true
        width: Math.min(page.width - 30, 700)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            Label {
                text: "Выберите начального студента и работу. Вставьте строки из Excel. Укажите общий вариант или запишите его в каждой ячейке как вариант:балл. Пустые ячейки очищают результат."
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            EntityPickerField {
                id: pasteStudent
                Layout.fillWidth: true
                rows: pasteDialog.students
                keyField: "student_id"
                labelField: "full_name"
                pickerTitle: "Выберите начального студента"
                searchPlaceholder: "ФИО, группа или № зачётки"
                columns: [
                    {key: "full_name", title: "Студент", stretch: true, minimum: 180},
                    {key: "group", title: "Группа", center: true, minimum: 80},
                    {key: "record_book_number", title: "№ зачётки", minimum: 100}
                ]
                value: pasteDialog.studentId
                onChosen: id => pasteDialog.studentId = id
            }
            OfficeComboBox {
                id: pasteWork
                Layout.fillWidth: true
                model: pasteDialog.works
                textRole: "title"
                valueRole: "id"
                currentIndex: Math.max(0, indexOfValue(page.draft("paste_work", 0)))
                onActivated: page.put("paste_work", currentValue)
            }
            OfficeTextField {
                Layout.fillWidth: true
                placeholderText: "Общий вариант для вставляемых баллов"
                text: page.draft("paste_variant", "")
                onTextEdited: page.put("paste_variant", text)
            }
            TextArea {
                id: pasteInput
                Layout.fillWidth: true
                Layout.preferredHeight: 180
                text: page.draft("paste", "")
                onTextChanged: if (activeFocus)
                    page.put("paste", text)
                placeholderText: "Ctrl+V — вставить баллы"
            }
            OfficeButton {
                text: "Сохранить прямоугольник"
                enabled: !app.busy && !!pasteDialog.studentId && pasteWork.currentIndex >= 0
                onClicked: page.send("paste_scores", {
                    student_ids: pasteDialog.students.map(row => row.student_id),
                    work_ids: pasteDialog.works.map(work => work.id),
                    row: pasteDialog.students.findIndex(row => row.student_id === pasteDialog.studentId),
                    column: pasteWork.currentIndex,
                    variant: page.draft("paste_variant", "").trim() || null,
                    text: page.draft("paste", "")
                })
            }
        }
    }
    Dialog {
        id: coefficientsDialog
        title: "Веса критериев, работ и вклады уровней"
        modal: true
        width: Math.min(page.width - 30, 900)
        height: Math.min(page.height - 30, 700)
        anchors.centerIn: parent
        contentItem: ScrollView {
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                Label {
                    text: "Границы и закрепление используются подбором. Исходные баллы и уровни карточек сохраняются."
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
                Repeater {
                    model: page.grading.parameters ? page.grading.parameters.coefficients : []
                    RowLayout {
                        required property var modelData
                        required property int index
                        Layout.fillWidth: true
                        Label {
                            text: modelData.title
                            Layout.preferredWidth: 280
                            wrapMode: Text.Wrap
                        }
                        OfficeTextField {
                            Layout.preferredWidth: 80
                            text: String((page.parameters.coefficients || [])[index] ? page.parameters.coefficients[index].value : modelData.value)
                            onTextEdited: page.setCoefficient(index, "value", text)
                        }
                        OfficeTextField {
                            Layout.preferredWidth: 70
                            text: String((page.parameters.coefficients || [])[index] ? page.parameters.coefficients[index].minimum : modelData.minimum)
                            onTextEdited: page.setCoefficient(index, "minimum", text)
                        }
                        OfficeTextField {
                            Layout.preferredWidth: 70
                            text: String((page.parameters.coefficients || [])[index] ? page.parameters.coefficients[index].maximum : modelData.maximum)
                            onTextEdited: page.setCoefficient(index, "maximum", text)
                        }
                        CheckBox {
                            text: "Закрепить"
                            checked: !!((page.parameters.coefficients || [])[index] || {}).locked
                            onToggled: page.setCoefficient(index, "locked", checked)
                        }
                    }
                }
                OfficeButton {
                    text: "Предпросмотр"
                    enabled: !app.busy
                    onClicked: page.preview()
                }
            }
        }
    }
    Dialog {
        id: templateDialog
        title: "Сохранить вариант расчёта"
        modal: true
        width: Math.min(page.width - 30, 500)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            OfficeTextField {
                placeholderText: "Название варианта"
                text: page.draft("template_name", "")
                onTextEdited: page.put("template_name", text)
            }
            OfficeButton {
                text: "Сохранить"
                enabled: !app.busy
                onClicked: page.send("save_template", {
                    name: page.draft("template_name", ""),
                    parameters: page.parameters,
                    parameter_version: page.parameters.version
                })
            }
        }
    }
    Dialog {
        id: loadTemplateDialog
        title: "Загрузить вариант расчёта"
        modal: true
        width: Math.min(page.width - 30, 500)
        anchors.centerIn: parent
        contentItem: ColumnLayout {
            OfficeComboBox {
                id: templateChoice
                model: page.grading.templates || []
                textRole: "name"
            }
            OfficeButton {
                text: "Загрузить"
                enabled: !app.busy && templateChoice.currentIndex >= 0
                onClicked: page.send("load_template", {
                    name: templateChoice.currentText,
                    revision: page.draft("revision", 0)
                })
            }
        }
    }
    FileDialog {
        id: exportDialog
        property int courseId: 0
        title: "Экспорт журнала XLSX"
        fileMode: FileDialog.SaveFile
        nameFilters: ["Excel (*.xlsx)"]
        defaultSuffix: "xlsx"
        onAccepted: page.sendAt(courseId, "export", {
            path: selectedFile.toString()
        })
    }
    Shortcut {
        sequence: "Ctrl+V"
        enabled: page.visible && page.activeSection === 1 && !page.modalOpen
        onActivated: {
            pasteDialog.pasteOnOpen = true;
            pasteDialog.open();
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    required property var app
    readonly property var snapshot: app.qmlState !== undefined ? app.qmlState : app.state
    required property var windows
    readonly property var interfaceZoom: windows.interfaceZoom
    readonly property real interfaceScale: interfaceZoom.factor
    font.pointSize: 12 * interfaceScale
    width: 1280; height: 800; minimumWidth: 980; minimumHeight: 640
    visibility: Window.Maximized; color: "#f3f6fa"
    title: "Учёт посещаемости — " + ((snapshot.system || {}).demo ? "ИМИТАТОР · тестовая база" : "Приватное управление")
    readonly property bool modalOpen: registrationPage.modalOpen || coursePage.modalOpen || journalPage.modalOpen || studentsPage.modalOpen || gradingPage.modalOpen || systemPage.modalOpen || commandsDialog.visible || courseBox.pickerOpen
    readonly property bool privateCommands: root.active && !root.modalOpen && app.ready
    function focusSearch() {
        const pages = [registrationPage, journalPage, gradingPage, coursePage]
        pages[tabs.currentIndex].focusSearch()
    }
    function confirm() {
        if (tabs.currentIndex === 0) registrationPage.confirmSelection()
        else if (tabs.currentIndex === 1) journalPage.confirmSelection()
    }
    function focusRegion(direction) {
        const items = [courseBox, tabs, content.children[content.currentIndex]]
        let current = root.activeFocusItem
        while (current && !items.includes(current)) current = current.parent
        const index = items.indexOf(current)
        items[(index + direction + items.length) % items.length].forceActiveFocus(Qt.TabFocusReason)
    }
    function showStudents(importList) {
        studentsWindow.showNormal()
        studentsWindow.requestActivate()
        if (importList) Qt.callLater(() => studentsPage.openImport())
    }
    function showSystem(section) {
        systemPage.openSection(section)
        settingsWindow.showNormal()
        settingsWindow.requestActivate()
    }
    onClosing: close => { if (!app.shutdownComplete) { close.accepted = false; app.requestClose() } }
    Connections { target: app; function onShutdownReady() { studentsWindow.hide(); settingsWindow.hide(); gradingPage.closeReport(); Qt.callLater(() => root.close()) } }

    component MainMenu: Menu {
        font.pixelSize: Math.round(18 * root.interfaceScale)
    }

    menuBar: MenuBar {
        objectName: "mainMenuBar"
        font.pixelSize: Math.round(18 * root.interfaceScale)
        MainMenu {
            title: "Файл"
            Action { text: "Импорт списка студентов…"; onTriggered: root.showStudents(true) }
            Action { text: "Экспорт журнала в Excel…"; enabled: !!app.selectedCourseId; onTriggered: journalPage.exportJournal() }
            MenuSeparator {}
            Action { text: "Создать резервную копию…"; enabled: !app.busy; onTriggered: { root.showSystem(2); Qt.callLater(() => systemPage.createBackup()) } }
            Action { text: "Восстановить из копии…"; enabled: !app.busy; onTriggered: { root.showSystem(2); Qt.callLater(() => systemPage.chooseRestore()) } }
            MenuSeparator {}
            Action { text: "Выход"; onTriggered: root.close() }
        }
        MainMenu {
            title: "Вид"
            Action {
                text: "Полноэкранный режим"
                shortcut: "F11"
                checkable: true
                checked: root.visibility === Window.FullScreen
                onTriggered: root.visibility === Window.FullScreen ? root.showMaximized() : root.showFullScreen()
            }
            Action { text: "Публичное окно"; checkable: true; checked: windows.publicVisible; onTriggered: windows.togglePublic() }
            Action { text: "Публичное окно на весь экран"; onTriggered: windows.setFullscreen(true) }
            MainMenu {
                title: "Масштаб интерфейса · " + root.interfaceZoom.percent + " %"
                Action { text: "Увеличить · Ctrl++"; onTriggered: root.interfaceZoom.zoomIn() }
                Action { text: "Уменьшить · Ctrl+-"; onTriggered: root.interfaceZoom.zoomOut() }
                Action { text: "Сбросить · Ctrl+0"; onTriggered: root.interfaceZoom.reset() }
            }
        }
        MainMenu {
            title: "Сервис"
            Action { text: "Настройки…"; onTriggered: root.showSystem(0) }
            Action { text: "Студенты и справочники…"; onTriggered: root.showStudents(false) }
            Action { text: "События считывателя выбранного занятия…"; enabled: !!app.selectedLessonId; onTriggered: root.showSystem(1) }
            MenuSeparator {}
            MainMenu {
                title: "Данные прежнего формата"
                Action { text: "Архив посещаемости…"; onTriggered: root.showSystem(1) }
                Action { text: "Экспорт CSV…"; onTriggered: root.showSystem(3) }
            }
        }
        MainMenu {
            title: "Команды · F1"
            Action { text: "Список команд"; onTriggered: commandsDialog.open() }
        }
    }
    ColumnLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
        anchors.topMargin: 6; anchors.bottomMargin: 6; spacing: 7
        RowLayout {
            Image { source: "../assets/bsu-wordmark.png"; Layout.preferredWidth: 82; Layout.preferredHeight: 32; fillMode: Image.PreserveAspectFit }
            Label { text: "Учёт посещаемости"; color: "#17243c"; font.pixelSize: Math.round(23 * root.interfaceScale); font.bold: true }
            Item { Layout.fillWidth: true }
            BusyIndicator { running: app.busy; visible: running; implicitWidth: 24; implicitHeight: 24 }
        }
        RowLayout {
            Label { text: "Курс:" }
            EntityPickerField {
                id: courseBox; objectName: "courseSelector"
                rows: (snapshot.courses || []).map(course => Object.assign({}, course, {
                    label: course.name + " · " + course.study_course + " курс · семестр " + course.semester
                }))
                columns: [
                    {key: "name", title: "Название", minimum: 160, stretch: true},
                    {key: "study_course", title: "Курс", numeric: true, center: true},
                    {key: "semester", title: "Семестр", numeric: true, center: true}
                ]
                labelField: "label"
                pickerTitle: "Выберите семестровый курс"
                placeholder: rows.length ? pickerTitle : "Создайте семестровый курс"
                searchPlaceholder: "Название, курс или семестр"
                value: app.selectedCourseId
                enabled: rows.length > 0
                onChosen: id => app.selectCourse(id)
            }
        }
        Rectangle {
            visible: !!(snapshot.registration || {}).open
            Layout.fillWidth: true; implicitHeight: activeLabel.implicitHeight + 14
            color: "#e8f0fb"; radius: 4
            Label {
                id: activeLabel; anchors.fill: parent; anchors.margins: 7
                text: "Регистрация открыта: " + ((snapshot.registration || {}).active_label || "")
                textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "#234667"
            }
        }
        Frame {
            visible: !!app.error || !!app.refreshError
            Layout.fillWidth: true
            RowLayout {
                anchors.fill: parent
                Label { text: app.error || ("Данные сохранены, но экран не обновлён: " + app.refreshError); textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "#991b1b"; Layout.fillWidth: true }
                OfficeButton { visible: app.canRetry; text: "Повторить ту же операцию"; onClicked: app.retry() }
                OfficeButton { visible: app.canRetry; text: "Проверить исход и изменить ввод"; onClicked: app.resolveFailed() }
                OfficeButton { visible: !!app.refreshError; text: "Обновить экран"; onClicked: app.command("view.refresh", ({})) }
            }
        }
        TabBar {
            id: tabs; objectName: "workspaceNavigation"; Layout.fillWidth: true
            OfficeTabButton { text: "Занятие"; width: implicitWidth; font.bold: checked }
            OfficeTabButton { text: "Посещаемость"; width: implicitWidth; font.bold: checked }
            OfficeTabButton { text: "Оценки"; width: implicitWidth; font.bold: checked }
            OfficeTabButton { text: "Курс"; width: implicitWidth; font.bold: checked }
        }
        Frame {
            Layout.fillWidth: true; Layout.fillHeight: true; padding: 10
            background: Rectangle { color: "white"; border.color: "#d8e2f0"; radius: 4 }
            StackLayout {
                id: content; anchors.fill: parent; currentIndex: tabs.currentIndex
                RegistrationPage { id: registrationPage; objectName: "registrationPage"; app: root.app; onHistoryRequested: (cells, occurrenceId) => journalPage.openHistory(cells, occurrenceId) }
                JournalPage { id: journalPage; objectName: "journalPage"; app: root.app }
                GradingPage { id: gradingPage; objectName: "gradingPage"; app: root.app; interfaceZoom: root.interfaceZoom }
                CoursePage {
                    id: coursePage; objectName: "coursePage"; app: root.app
                    onStudentsRequested: root.showStudents(false)
                    onWrittenWorkRequested: { tabs.currentIndex = 2; gradingPage.openNewWork() }
                    onLessonRequested: lessonId => { registrationPage.revealLesson(); app.selectLesson(lessonId); tabs.currentIndex = 0 }
                }
            }
        }
    }
    footer: Label {
        text: "Считыватель: " + ((snapshot.system || {}).reader_status || "не подключён")
        color: "#62718a"; padding: 4; textFormat: Text.PlainText
    }
    ApplicationWindow {
        id: studentsWindow; objectName: "studentsWindow"
        readonly property real interfaceScale: root.interfaceScale
        transientParent: root; title: "Студенты и справочники"
        width: 1100; height: 760; minimumWidth: 900; minimumHeight: 620
        visible: false; color: root.color; font: root.font; palette: root.palette
        ZoomShortcuts { zoom: root.interfaceZoom }
        Shortcut { sequence: "F3"; enabled: studentsWindow.active && !studentsPage.modalOpen; onActivated: studentsPage.focusSearch() }
        StudentsPage { id: studentsPage; objectName: "studentsPage"; anchors.fill: parent; anchors.margins: 10; app: root.app }
    }
    ApplicationWindow {
        id: settingsWindow; objectName: "settingsWindow"
        readonly property real interfaceScale: root.interfaceScale
        transientParent: root; title: "Настройки и обслуживание"
        width: 1100; height: 760; minimumWidth: 900; minimumHeight: 620
        visible: false; color: root.color; font: root.font; palette: root.palette
        ZoomShortcuts { zoom: root.interfaceZoom }
        Shortcut { sequence: "F3"; enabled: settingsWindow.active && !systemPage.modalOpen; onActivated: systemPage.focusSearch() }
        SystemPage { id: systemPage; objectName: "systemPage"; anchors.fill: parent; anchors.margins: 10; app: root.app; windows: root.windows }
    }
    ZoomShortcuts { zoom: root.interfaceZoom }
    Shortcut { sequence: "F1"; enabled: root.privateCommands; onActivated: commandsDialog.open() }
    Shortcut { sequence: "F2"; enabled: root.privateCommands && tabs.currentIndex === 0 && app.canManual; onActivated: app.manualSelected() }
    Shortcut { sequence: "F3"; enabled: root.privateCommands; onActivated: root.focusSearch() }
    Shortcut { sequence: "F4"; enabled: root.privateCommands; onActivated: { tabs.currentIndex = 0; registrationPage.showMatching(); app.correctLastCard() } }
    Shortcut { sequence: "F9"; enabled: root.privateCommands && app.canOpen; onActivated: app.command("registration.open", ({})) }
    Shortcut { sequence: "Shift+F9"; enabled: root.privateCommands && app.canClose; onActivated: app.command("registration.close", ({})) }
    Shortcut { sequence: "Ctrl+Return"; enabled: root.privateCommands; onActivated: root.confirm() }
    Shortcut { sequence: "Escape"; enabled: root.privateCommands && tabs.currentIndex === 0; onActivated: app.deferMatch() }
    Shortcut { sequence: "F6"; enabled: root.privateCommands; onActivated: root.focusRegion(1) }
    Shortcut { sequence: "Shift+F6"; enabled: root.privateCommands; onActivated: root.focusRegion(-1) }
    Dialog {
        id: commandsDialog; title: "Команды"; modal: true
        anchors.centerIn: parent; width: Math.min(root.width - 80, 620)
        standardButtons: Dialog.Close
        ColumnLayout {
            width: parent.width
            Label { text: "F2 — посещение выбранного студента\nF3 — поиск\nF4 — исправить последнюю карту\nF9 / Shift+F9 — открыть / закрыть регистрацию\nCtrl+Enter — подтвердить выбранное\nEnter / Пробел — отметить или отменить посещение в журнале\nDelete / Backspace — очистить выбранную ручную отметку или результат\nF6 / Shift+F6 — перейти между областями\nShift+стрелки — выделить прямоугольник\nEsc — отложить карту или закрыть диалог"; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
    }
}

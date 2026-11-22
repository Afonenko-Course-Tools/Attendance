pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "TableKeys.js" as TableKeys

Control {
    id: table
    required property var rows
    required property var columns
    property string query: ""
    property var visibleRows: queryRows(rows, query)
    property var activeId: null
    property var selectedIds: []
    property var customWidths: ({})
    property bool adjustableColumns: true
    property var checkedIds: []
    property bool checkable: false
    property bool removalEnabled: false
    property string keyField: "id"
    property string sortField: ""
    property bool descending: false
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    readonly property int checkWidth: checkable ? Math.round(38 * interfaceScale) : 0
    readonly property var orderedRows: {
        const source = visibleRows || [];
        const column = columns.find(entry => entry.key === sortField);
        if (!column)
            return source;
        return Array.from(source).sort((a, b) => {
            const left = a[column.sortKey || column.key];
            const right = b[column.sortKey || column.key];
            if (left === undefined || left === null || left === "")
                return right === undefined || right === null || right === "" ? 0 : 1;
            if (right === undefined || right === null || right === "")
                return -1;
            const cmp = column.numeric ? Number(left) - Number(right) : String(left).localeCompare(String(right));
            return descending ? -cmp : cmp;
        });
    }
    readonly property var fittedWidths: {
        const scale = interfaceScale;
        const widths = columns.map(column => {
            if (customWidths[column.key])
                return Math.round(customWidths[column.key] * scale);
            const sizes = (rows || []).map(row => metrics.advanceWidth(display(row[column.key]))).sort((a, b) => a - b);
            const fit = (sizes[Math.ceil(sizes.length * 0.9) - 1] || 0) + 22 * scale;
            const title = metrics.advanceWidth(column.title + " ↓") + 16 * scale;
            return Math.max(title, Math.min((column.maximum || 310) * scale, Math.max((column.minimum || 40) * scale, fit)));
        });
        let remaining = Math.max(0, width - checkWidth - widths.reduce((a, b) => a + b, 0) - 2);
        const stretch = columns.filter(column => column.stretch && !customWidths[column.key]).length;
        if (stretch > 0) {
            for (let i = 0; i < columns.length; ++i)
                if (columns[i].stretch && !customWidths[columns[i].key])
                    widths[i] += remaining / stretch;
        }
        return widths;
    }
    readonly property int count: orderedRows.length

    signal selected(var row, bool extend)
    signal activated(var row)
    signal checked(var row, bool value)
    signal removalRequested(var row)

    function display(value) {
        return value === undefined || value === null || value === "" ? "—" : String(value);
    }
    function normalize(value) {
        return String(value ?? "").toLocaleLowerCase().replace(/ё/g, "е");
    }
    function queryRows(source, text) {
        const tokens = normalize(text).trim().split(/\s+/).filter(Boolean);
        if (!tokens.length)
            return source || [];
        return (source || []).filter(row => {
            const haystack = normalize(row.search_text ?? columns.map(column => row[column.key] ?? "").join(" "));
            return tokens.every(token => haystack.includes(token));
        });
    }
    function selectRow(row, extend) {
        if (row)
            selected(row, !!extend);
    }
    function activateRow(row) {
        if (!row)
            return;
        selectRow(row);
        if (checkable)
            checked(row, !checkedIds.includes(row[keyField]));
        else
            activated(row);
    }
    function focusFirst() {
        if (!orderedRows.length)
            return;
        list.currentIndex = Math.max(0, orderedRows.findIndex(row => row[keyField] === activeId));
        list.forceActiveFocus();
        selectRow(orderedRows[list.currentIndex]);
    }
    function move(step) {
        if (!orderedRows.length)
            return;
        const current = orderedRows.findIndex(row => row[keyField] === activeId);
        const next = current < 0 ? 0 : Math.min(Math.max(0, current + step), orderedRows.length - 1);
        list.currentIndex = next;
        selectRow(orderedRows[next]);
        list.positionViewAtIndex(next, ListView.Contain);
    }

    function resizeColumn(key, pixels) {
        const updated = Object.assign({}, customWidths);
        if (pixels > 0)
            updated[key] = Math.max(48, Math.round(pixels / interfaceScale));
        else
            delete updated[key];
        customWidths = updated;
    }

    FontMetrics { id: metrics; font: table.font }
    background: Rectangle { color: "white"; border.color: "#dbe2ed" }
    contentItem: ListView {
        id: list
        model: table.orderedRows
        clip: true
        keyNavigationEnabled: false
        boundsBehavior: Flickable.StopAtBounds
        currentIndex: -1
        contentWidth: Math.max(width, table.checkWidth + table.fittedWidths.reduce((a, b) => a + b, 0))
        flickableDirection: Flickable.HorizontalAndVerticalFlick
        headerPositioning: ListView.OverlayHeader
        header: Row {
            height: Math.round(36 * table.interfaceScale)
            z: 2
            OfficeTableHeader {
                visible: table.checkable
                width: table.checkWidth
                height: parent.height
            }
            Repeater {
                model: table.columns
                OfficeTableHeader {
                    id: heading
                    required property var modelData
                    required property int index
                    width: table.fittedWidths[index] || 40
                    height: parent.height
                    text: modelData.title + (table.sortField === modelData.key ? (table.descending ? " ↓" : " ↑") : "")
                    TapHandler {
                        onTapped: {
                            table.descending = table.sortField === heading.modelData.key && !table.descending;
                            table.sortField = heading.modelData.key;
                        }
                    }
                    MouseArea {
                        enabled: table.adjustableColumns
                        anchors.right: parent.right
                        width: 7
                        height: parent.height
                        cursorShape: Qt.SplitHCursor
                        property real startX
                        property real startWidth
                        onPressed: mouse => {
                            startX = mapToItem(table, mouse.x, 0).x;
                            startWidth = heading.width;
                        }
                        onPositionChanged: mouse => {
                            if (pressed)
                                table.resizeColumn(heading.modelData.key, startWidth + mapToItem(table, mouse.x, 0).x - startX);
                        }
                        onDoubleClicked: table.resizeColumn(heading.modelData.key, 0)
                    }
                }
            }
        }
        delegate: ItemDelegate {
            id: rowDelegate
            required property var modelData
            required property int index
            width: list.contentWidth
            height: Math.round(33 * table.interfaceScale)
            padding: 0
            highlighted: modelData[table.keyField] === table.activeId || table.selectedIds.includes(modelData[table.keyField])
            Accessible.name: table.columns.map(column => table.display(modelData[column.key])).join(", ")
            background: Rectangle {
                color: rowDelegate.highlighted ? "#deebfa" : rowDelegate.index % 2 ? "#f5f8fc" : "white"
            }
            contentItem: Row {
                CheckBox {
                    visible: table.checkable
                    width: table.checkWidth
                    height: rowDelegate.height
                    checked: table.checkedIds.includes(rowDelegate.modelData[table.keyField])
                    Accessible.name: rowDelegate.Accessible.name
                    onToggled: {
                        list.currentIndex = rowDelegate.index;
                        table.selectRow(rowDelegate.modelData);
                        table.checked(rowDelegate.modelData, checked);
                    }
                }
                Repeater {
                    model: table.columns
                    OfficeTableCell {
                        required property var modelData
                        required property int index
                        width: table.fittedWidths[index] || 40
                        height: rowDelegate.height
                        text: table.display(rowDelegate.modelData[modelData.key])
                        horizontalAlignment: modelData.center ? Text.AlignHCenter : Text.AlignLeft
                    }
                }
            }
            TapHandler {
                onTapped: {
                    list.currentIndex = rowDelegate.index;
                    list.forceActiveFocus();
                    table.selectRow(rowDelegate.modelData, (point.modifiers & Qt.ShiftModifier) !== 0);
                }
                onDoubleTapped: table.activateRow(rowDelegate.modelData)
            }
        }
        Keys.onDownPressed: table.move(1)
        Keys.onUpPressed: table.move(-1)
        Keys.onReturnPressed: table.activateRow(table.orderedRows[currentIndex])
        Keys.onEnterPressed: table.activateRow(table.orderedRows[currentIndex])
        Keys.onSpacePressed: if (table.checkable) table.activateRow(table.orderedRows[currentIndex])
        Keys.onPressed: event => {
            if ((!table.checkable && !table.removalEnabled) || !TableKeys.isClearKey(event))
                return;
            event.accepted = true;
            if (event.isAutoRepeat)
                return;
            const row = table.orderedRows.find(item => item[table.keyField] === table.activeId);
            if (!row)
                return;
            if (table.checkable && table.checkedIds.includes(row[table.keyField]))
                table.checked(row, false);
            else if (table.removalEnabled)
                table.removalRequested(row);
        }
        ScrollBar.vertical: ScrollBar {}
        ScrollBar.horizontal: ScrollBar {}
    }

    Label {
        anchors.centerIn: parent
        width: parent.width - 30
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.Wrap
        text: "Ничего не найдено"
        color: "#62718a"
        visible: !table.count
    }
}

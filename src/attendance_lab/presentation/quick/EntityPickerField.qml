import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

OfficeButton {
    id: field
    required property var rows
    required property var columns
    property string pickerTitle: "Выберите запись"
    property string placeholder: pickerTitle + "…"
    property string searchPlaceholder: "Поиск"
    property string labelField: "name"
    property string keyField: "id"
    property var value: null
    readonly property bool pickerOpen: picker.visible
    readonly property var currentRow: (rows || []).find(row => row[keyField] === value)
    signal chosen(var id)

    contentItem: RowLayout {
        spacing: 8
        Label {
            id: valueLabel
            Layout.fillWidth: true
            text: field.text
            font: field.font
            textFormat: Text.PlainText
            color: !field.enabled ? "#7b8798" : field.primary ? "white" : "#234667"
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
        Label {
            text: "▾"
            font: field.font
            color: !field.enabled ? "#7b8798" : field.primary ? "white" : "#234667"
        }
    }
    text: currentRow ? String(currentRow[labelField] ?? currentRow.name ?? "—") : placeholder
    ToolTip.text: text
    ToolTip.visible: hovered && valueLabel.truncated
    Layout.fillWidth: true
    Layout.minimumWidth: Math.round(150 * interfaceScale)
    implicitWidth: Math.round(320 * interfaceScale)
    onClicked: picker.open()

    Dialog {
        id: picker
        property var pendingId: null
        readonly property bool canConfirm: lookup.orderedRows.some(row => row[field.keyField] === pendingId)
        title: field.pickerTitle
        modal: true
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(800, Math.max(240, (parent ? parent.width : 800) - 28))
        height: Math.min(580, Math.max(240, (parent ? parent.height : 580) - 28))
        onOpened: {
            search.clear();
            pendingId = field.value;
            search.forceActiveFocus();
        }
        function choose(id) {
            field.chosen(id);
            close();
        }
        contentItem: ColumnLayout {
            OfficeSearchField {
                id: search
                placeholderText: field.searchPlaceholder
                Keys.onDownPressed: lookup.focusFirst()
                onAccepted: lookup.focusFirst()
            }
            OfficeDataTable {
                id: lookup
                Layout.fillWidth: true
                Layout.fillHeight: true
                rows: field.rows
                columns: field.columns
                query: search.text
                keyField: field.keyField
                activeId: picker.pendingId
                onSelected: row => picker.pendingId = row[field.keyField]
                onActivated: row => picker.choose(row[field.keyField])
            }
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: "Показано " + lookup.count + " из " + (field.rows || []).length
                }
                OfficeButton {
                    text: "Выбрать"
                    primary: true
                    enabled: picker.canConfirm
                    onClicked: picker.choose(picker.pendingId)
                }
                OfficeButton { text: "Отмена"; onClicked: picker.close() }
            }
        }
    }
}

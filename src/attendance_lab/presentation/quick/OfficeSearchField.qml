import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

OfficeTextField {
    id: control
    signal queryEdited(string query)

    Layout.fillWidth: true
    Layout.minimumWidth: Math.round(160 * interfaceScale)
    implicitWidth: Math.round(320 * interfaceScale)
    rightPadding: clearButton.visible ? clearButton.width + 3 : padding
    selectByMouse: true
    onTextEdited: queryEdited(text)

    ToolButton {
        id: clearButton
        anchors.right: parent.right
        anchors.rightMargin: 2
        anchors.verticalCenter: parent.verticalCenter
        width: Math.round(32 * control.interfaceScale)
        height: parent.height - 4
        visible: !!control.text
        text: "×"
        activeFocusOnTab: false
        Accessible.name: "Очистить поиск"
        ToolTip.text: Accessible.name
        ToolTip.visible: hovered
        onClicked: {
            control.clear()
            control.queryEdited("")
            control.forceActiveFocus()
        }
    }
}

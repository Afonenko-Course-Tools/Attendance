import QtQuick
import QtQuick.Controls as Controls

// Keep native text editing, validation, clipboard and input-method behavior.
Controls.TextField {
    id: control
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    font.family: "Arial"
    font.pixelSize: Math.round(16 * interfaceScale)
    padding: Math.round(6 * interfaceScale)
    implicitHeight: Math.max(Math.round(36 * interfaceScale), contentHeight + topPadding + bottomPadding + 2)
    color: enabled ? "#17243c" : "#7b8798"
    placeholderTextColor: "#62718a"
    selectionColor: "#1768b8"
    selectedTextColor: "white"
    verticalAlignment: TextInput.AlignVCenter
    background: Rectangle {
        implicitWidth: 120
        radius: 4
        color: "white"
        border.width: 1
        border.color: control.activeFocus ? "#1768b8" : "#cbd5e2"
    }
}

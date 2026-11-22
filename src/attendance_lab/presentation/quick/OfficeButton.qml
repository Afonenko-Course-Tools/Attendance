import QtQuick
import QtQuick.Controls as Controls

// The QPushButton rules from presentation/common.py, retaining the Controls API.
Controls.Button {
    id: control
    property bool primary: false
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    font.family: "Arial"
    font.pixelSize: Math.round(16 * interfaceScale)
    font.weight: primary ? Font.DemiBold : Font.Normal
    hoverEnabled: true
    leftPadding: Math.round(13 * interfaceScale)
    rightPadding: leftPadding
    topPadding: Math.round(8 * interfaceScale)
    bottomPadding: topPadding
    implicitWidth: Math.max(Math.round(80 * interfaceScale), implicitContentWidth + leftPadding + rightPadding + 2)
    implicitHeight: Math.max(Math.round(38 * interfaceScale), implicitContentHeight + topPadding + bottomPadding + 2)
    palette.buttonText: !enabled ? "#9ba6b4" : primary ? "white" : "#234667"
    background: Rectangle {
        radius: 5
        color: !control.enabled ? "#f1f3f6" : control.primary ? "#1768b8" : control.down ? "#d8e7f8" : control.hovered ? "#e8f0fa" : "white"
        border.width: 1
        border.color: control.primary && control.enabled ? "#1768b8" : control.visualFocus && control.enabled ? "#1768b8" : control.hovered && control.enabled ? "#90abc9" : "#c8d3e2"
    }
}

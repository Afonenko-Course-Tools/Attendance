import QtQuick
import QtQuick.Controls as Controls

// Natural-width tabs match QTabBar padding and its existing selected colors.
Controls.TabButton {
    id: control
    readonly property real interfaceScale: Window.window?.interfaceScale ?? 1
    width: implicitWidth
    font.family: "Arial"
    font.pixelSize: Math.round(16 * interfaceScale)
    font.weight: checked ? Font.DemiBold : Font.Normal
    leftPadding: Math.round(17 * interfaceScale)
    rightPadding: leftPadding
    topPadding: Math.round(9 * interfaceScale)
    bottomPadding: topPadding
    implicitWidth: implicitContentWidth + leftPadding + rightPadding
    implicitHeight: Math.max(Math.round(38 * interfaceScale), implicitContentHeight + topPadding + bottomPadding)
    palette.buttonText: checked ? "#1455a0" : "#384b67"
    background: Rectangle {
        color: control.checked ? "white" : "#e7ecf4"
    }
}

import QtQuick
import QtQuick.Controls

Label {
    id: cell
    leftPadding: 5
    rightPadding: 5
    verticalAlignment: Text.AlignVCenter
    elide: Text.ElideRight
    textFormat: Text.PlainText
    color: "#17243c"
    background: Rectangle {
        color: "transparent"
        border.color: "#e8edf4"
    }

    ToolTip.text: text
    ToolTip.visible: truncated && hover.hovered
    HoverHandler { id: hover }
}

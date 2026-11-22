import QtQuick
import QtQuick.Controls

Label {
    id: heading
    padding: 8
    font.weight: Font.DemiBold
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    color: "#344b69"
    elide: Text.ElideRight
    textFormat: Text.PlainText
    background: Rectangle {
        color: "#eef3f9"
        border.color: "#d6e0ed"
    }

    ToolTip.text: text
    ToolTip.visible: truncated && hover.hovered
    HoverHandler { id: hover }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    required property var projection
    width: 920; height: 680
    visible: false
    color: "#f5f8fc"
    title: "Регистрация присутствия"
    Shortcut { sequence: "Esc"; onActivated: if (root.visibility === Window.FullScreen) root.showNormal() }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 48; spacing: 30
        Label { text: root.projection.context; textFormat: Text.PlainText; color: "#425a73"; font.pixelSize: 23; wrapMode: Text.Wrap; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
        Item { Layout.fillHeight: true }
        Label { text: root.projection.headline; textFormat: Text.PlainText; color: root.projection.color; font.pixelSize: 44; font.bold: true; wrapMode: Text.Wrap; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
        Label { text: root.projection.person; textFormat: Text.PlainText; color: root.projection.color; font.pixelSize: 36; wrapMode: Text.Wrap; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
        Label { text: root.projection.detail; textFormat: Text.PlainText; color: "#425a73"; font.pixelSize: 24; wrapMode: Text.Wrap; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter }
        Item { Layout.fillHeight: true }
    }
}

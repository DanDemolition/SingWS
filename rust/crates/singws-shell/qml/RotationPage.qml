import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page
    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }

    RotationPanel { id: panel }
    property var rows: []
    Connections {
        target: panel
        function onRowsJsonChanged() { page.rows = JSON.parse(panel.rowsJson) }
    }
    Component.onCompleted: panel.refresh(arg("--now-singing"))
    // the app saves the queue every so often; pick up changes without any write from this window
    Timer { interval: 5000; running: true; repeat: true; onTriggered: panel.refresh(page.arg("--now-singing")) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            Label { text: panel.titleLabel; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: panel.countLabel; color: "#a78bfa"; font.pixelSize: 13; font.weight: Font.DemiBold }
        }
        Label { text: panel.status; color: "#9a97c0"; font.pixelSize: 12 }
        Rectangle {
            visible: panel.nowStrip !== "" || panel.nextStrip !== ""
            Layout.fillWidth: true
            height: strips.implicitHeight + 16
            radius: 8
            color: "#12102a"
            border.color: "#2a2650"
            ColumnLayout {
                id: strips
                anchors.fill: parent
                anchors.margins: 8
                spacing: 2
                Label { visible: panel.nowStrip !== ""; text: "NOW  " + panel.nowStrip; color: "#ffffff"; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                Label { visible: panel.nextStrip !== ""; text: "NEXT  " + panel.nextStrip; color: "#c9c6e8"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            model: page.rows
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 46
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.number + ", " + modelData.singer + ", " + modelData.combined
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 12
                    Label { text: modelData.number; color: "#a78bfa"; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.preferredWidth: 26 }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { text: modelData.singer; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: modelData.combined; color: "#c9c6e8"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                    Label { visible: modelData.duet; text: "DUET"; color: "#9a97c0"; font.pixelSize: 11 }
                }
            }
        }
    }
}

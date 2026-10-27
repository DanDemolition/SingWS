import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    TickerPanel { id: panel }
    property var singers: JSON.parse(panel.singersJson)
    Connections { target: panel; function onSingersJsonChanged() { page.singers = JSON.parse(panel.singersJson) } }

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "TICKER (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap }

        // the exact string the audience ticker scrolls
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 56
            radius: 8
            color: "#12102a"
            border.color: "#2a2650"
            clip: true
            Label {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                verticalAlignment: Text.AlignVCenter
                text: panel.text
                color: "#ffffff"
                font.pixelSize: 20
                elide: Text.ElideRight
                Accessible.name: "Ticker text: " + panel.text
            }
        }

        Label { visible: panel.message !== ""; text: "Custom message: " + panel.message; color: "#c9c6e8"; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.Wrap }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.singers
            spacing: 2
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 32
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: (index + 1) + ". " + modelData
                Label { anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: 10; text: (index + 1) + ".  " + modelData; color: "#c9c6e8"; font.pixelSize: 13 }
            }
        }
    }
}

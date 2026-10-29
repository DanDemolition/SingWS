import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    TickerPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false})
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    // connected to the app: the message can be changed from here; otherwise the page stays read-only
    property bool live: cview.enabled === true
    function setSetting(label, key, value) { if (control) control.send(label, "settings.set", JSON.stringify({key: key, value: value}), false) }
    property var singers: JSON.parse(panel.singersJson)
    Connections { target: panel; function onSingersJsonChanged() { page.singers = JSON.parse(panel.singersJson) } }

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: page.live ? "TICKER" : "TICKER (read-only: not connected to the app)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
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

        // the custom message (the Ticker Settings dialog's "Include custom message" fields); only these two settings can be changed from here
        Rectangle {
            Layout.fillWidth: true
            visible: page.live
            height: msgCol.implicitHeight + 16
            radius: 8
            color: "#12102a"
            border.color: "#2a2650"
            ColumnLayout {
                id: msgCol
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6
                Label { text: "CUSTOM MESSAGE"; color: "#6d6a99"; font.pixelSize: 10; font.letterSpacing: 1.2 }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    TextField {
                        id: msgField
                        Layout.fillWidth: true
                        placeholderText: "Type a ticker message. Codes: %m_curSinger %curSong %nextSinger"
                        maximumLength: 200
                        selectByMouse: true
                        Accessible.name: "Custom ticker message"
                    }
                    Button { text: "Save message"; enabled: msgField.text.length <= 200; onClicked: page.setSetting("Save ticker message", "ticker_custom_message", msgField.text); Accessible.name: "Save the custom ticker message" }
                    Button { text: "Show on ticker"; onClicked: page.setSetting("Show the ticker message", "ticker_custom_enabled", true); Accessible.name: "Show the custom message on the ticker" }
                    Button { text: "Hide"; onClicked: page.setSetting("Hide the ticker message", "ticker_custom_enabled", false); Accessible.name: "Hide the custom message from the ticker" }
                }
                Rectangle {
                    Layout.fillWidth: true
                    visible: page.cview.result !== undefined && page.cview.result !== null
                    height: visible ? resultText.implicitHeight + 12 : 0
                    radius: 6
                    color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
                    Label { id: resultText; anchors.fill: parent; anchors.margins: 6; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 12; text: page.cview.result ? page.cview.result.text : "" }
                    Accessible.role: Accessible.StaticText
                    Accessible.name: page.cview.result ? page.cview.result.text : ""
                }
            }
        }

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

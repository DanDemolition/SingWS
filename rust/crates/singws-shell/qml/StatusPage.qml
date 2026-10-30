import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    StatusPanel { id: panel }
    property var view: JSON.parse(panel.viewJson)
    Connections { target: panel; function onViewJsonChanged() { page.view = JSON.parse(panel.viewJson) } }
    readonly property bool has: view.chips !== undefined
    // the header's colour names, as the Python app draws them
    function toneColor(t) { return t === "good" ? "#34e08a" : (t === "warn" ? "#f5c84b" : (t === "purple" ? "#a855f7" : "#969cb8")) }

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "STATUS (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: page.has && page.view.stale ? "#f5c84b" : "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; Accessible.name: "Status snapshot: " + panel.status }

        // the four pills of the Show tab's header
        Flow {
            Layout.fillWidth: true
            spacing: 8
            visible: page.has
            Repeater {
                model: page.has ? page.view.chips : []
                Rectangle {
                    height: 30
                    width: chipText.implicitWidth + 28
                    radius: 15
                    color: "#12102a"
                    border.color: page.toneColor(modelData.tone)
                    Accessible.role: Accessible.StaticText
                    Accessible.name: modelData.text
                    Label { id: chipText; anchors.centerIn: parent; text: modelData.text; color: page.toneColor(modelData.tone); font.pixelSize: 13; font.weight: Font.DemiBold }
                }
            }
        }

        // the status line and the venue, with the "Server diagnostics" tooltip written out
        Rectangle {
            Layout.fillWidth: true
            visible: page.has
            height: lineCol.implicitHeight + 20
            radius: 8
            color: "#12102a"
            border.color: "#2a2650"
            ColumnLayout {
                id: lineCol
                anchors.fill: parent
                anchors.margins: 10
                spacing: 6
                RowLayout {
                    spacing: 8
                    Rectangle { width: 10; height: 10; radius: 5; color: page.has && page.view.dotToken === "warning" ? "#f5c84b" : "#34e08a" }
                    Label { text: page.has ? page.view.statusText : ""; color: "#ffffff"; font.pixelSize: 14; Accessible.name: "Status line: " + text }
                    Label { text: page.has ? "• " + page.view.location : ""; color: "#9a97c0"; font.pixelSize: 14 }
                }
                Label { text: page.has ? page.view.tooltip : ""; color: "#c9c6e8"; font.pixelSize: 12; lineHeight: 1.15; wrapMode: Text.Wrap; Layout.fillWidth: true }
            }
        }

        // the SINGERS / ETA / ENDS line, in the app's own colours
        Rectangle {
            Layout.fillWidth: true
            visible: page.has
            height: 46
            radius: 8
            color: "#12102a"
            border.color: "#2a2650"
            Label {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                verticalAlignment: Text.AlignVCenter
                textFormat: Text.RichText
                text: page.has ? page.view.etaHtml : ""
                font.pixelSize: 16
                elide: Text.ElideRight
                Accessible.name: "Queue line: " + (page.has ? page.view.summary : "")
            }
        }
        Label { visible: page.has; text: page.has && page.view.summary !== "" ? "Queue summary: " + page.view.summary : "Queue summary: nobody is queued"; color: "#c9c6e8"; font.pixelSize: 13 }

        Item { Layout.fillHeight: true }
    }
}

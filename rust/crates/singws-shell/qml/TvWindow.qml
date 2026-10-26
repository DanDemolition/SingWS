import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import com.singws.shell

// The audience's singer-rotation screen, in a window of its own. It shows exactly what the Rotation tab shows (the same Rust view model, which is parity-tested against the
// app's RotationView) in the app's look: black, lime header, purple "now" card, big bold rows. It only READS the queue and the live stage state; it never writes anything.
// Keys: F or double-click = full screen on / off, Esc = leave full screen, S = move to the next screen.
Window {
    id: tv
    width: 1280
    height: 720
    visible: false
    title: "SingWS - Singer Rotation"
    color: "#000000"

    function arg(name) {
        var a = Qt.application.arguments
        for (var i = 0; i < a.length - 1; i++) if (a[i] === name) return a[i + 1]
        return ""
    }
    function toggleFullScreen() { visibility = (visibility === Window.FullScreen) ? Window.Windowed : Window.FullScreen }
    function nextScreen() {
        var s = Qt.application.screens
        if (s.length < 2) return
        var cur = 0
        for (var i = 0; i < s.length; i++) if (s[i].name === tv.screen.name) cur = i
        tv.screen = s[(cur + 1) % s.length]
    }
    // everything scales with the window: sizes below are for 1280 x 720
    property real u: Math.max(0.4, Math.min(width / 1280, height / 720))

    RotationPanel { id: panel }
    property var rows: []
    Connections { target: panel; function onRowsJsonChanged() { tv.rows = JSON.parse(panel.rowsJson) } }
    onVisibleChanged: if (visible) panel.refresh(arg("--now-singing"))
    // two seconds: the audience screen should follow the app quickly (the panel re-reads a file only when it changed)
    Timer { interval: 2000; running: tv.visible; repeat: true; onTriggered: panel.refresh(tv.arg("--now-singing")) }

    // when there are more singers than fit, show them a page at a time instead of leaving the bottom unreadable
    property int rowHeight: Math.round(86 * u)
    property int perPage: Math.max(1, Math.floor(listArea.height / rowHeight))
    property int page: 0
    property int pages: Math.max(1, Math.ceil(rows.length / perPage))
    Timer {
        interval: 7000
        running: tv.visible && tv.pages > 1
        repeat: true
        onTriggered: { tv.page = (tv.page + 1) % tv.pages; list.positionViewAtIndex(tv.page * tv.perPage, ListView.Beginning) }
    }
    onRowsChanged: { if (page >= pages) page = 0 }

    Rectangle { anchors.fill: parent; color: "#000000"; z: -1 }   // the content must carry its own black (a screenshot of the content would otherwise be transparent)

    Shortcut { sequence: "F"; onActivated: tv.toggleFullScreen() }
    Shortcut { sequence: "Esc"; onActivated: tv.visibility = Window.Windowed }
    Shortcut { sequence: "S"; onActivated: tv.nextScreen() }
    MouseArea { anchors.fill: parent; onDoubleClicked: tv.toggleFullScreen() }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Math.round(28 * tv.u)
        spacing: Math.round(14 * tv.u)

        RowLayout {
            Layout.fillWidth: true
            Label { text: panel.titleLabel.toUpperCase(); color: "#ADFF2F"; font.pixelSize: Math.round(16 * tv.u); font.weight: Font.ExtraBold; font.letterSpacing: 2 * tv.u; Layout.fillWidth: true; elide: Text.ElideRight }
            Label { text: panel.countLabel; color: "#AAA5B8"; font.pixelSize: Math.round(16 * tv.u); font.weight: Font.Bold }
        }

        // NOW card
        Rectangle {
            visible: panel.nowStrip !== ""
            Layout.fillWidth: true
            height: nowCol.implicitHeight + Math.round(36 * tv.u)
            radius: Math.round(22 * tv.u)
            border.color: Qt.rgba(160 / 255, 135 / 255, 255 / 255, 0.5)
            border.width: 1
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Qt.rgba(104 / 255, 75 / 255, 205 / 255, 0.55) }
                GradientStop { position: 1.0; color: Qt.rgba(44 / 255, 34 / 255, 83 / 255, 0.75) }
            }
            ColumnLayout {
                id: nowCol
                anchors.fill: parent
                anchors.margins: Math.round(18 * tv.u)
                spacing: Math.round(4 * tv.u)
                Label { text: "NOW SINGING"; color: "#ADFF2F"; font.pixelSize: Math.round(13 * tv.u); font.weight: Font.ExtraBold; font.letterSpacing: 2 * tv.u }
                Label { text: panel.nowStrip; color: "#FFFFFF"; font.pixelSize: Math.round(40 * tv.u); font.weight: Font.ExtraBold; elide: Text.ElideRight; Layout.fillWidth: true }
                Label { visible: panel.nextStrip !== ""; text: "NEXT   " + panel.nextStrip; color: "#BDB4DB"; font.pixelSize: Math.round(22 * tv.u); font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
            }
        }

        Label {
            visible: tv.rows.length === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: panel.status !== "" ? panel.status : "Nobody is waiting"
            color: "#6a678f"
            font.pixelSize: Math.round(30 * tv.u)
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
        }

        Item {
            id: listArea
            Layout.fillWidth: true
            Layout.fillHeight: true
        ListView {
            id: list
            visible: tv.rows.length > 0
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            height: tv.perPage * tv.rowHeight     // whole rows only: a half-cut row at the bottom of a TV looks like a mistake
            clip: true
            interactive: false
            model: tv.rows
            delegate: Rectangle {
                width: list.width
                height: tv.rowHeight
                radius: Math.round(14 * tv.u)
                color: index === 0 && panel.nowStrip === "" ? Qt.rgba(46 / 255, 114 / 255, 214 / 255, 0.67) : Qt.rgba(1, 1, 1, 0.04)
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.number + ", " + modelData.singer + ", " + modelData.combined
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Math.round(20 * tv.u)
                    anchors.rightMargin: Math.round(20 * tv.u)
                    spacing: Math.round(18 * tv.u)
                    Label { text: modelData.number; color: "#A78BFA"; font.pixelSize: Math.round(34 * tv.u); font.weight: Font.ExtraBold; Layout.preferredWidth: Math.round(60 * tv.u); horizontalAlignment: Text.AlignRight }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { text: modelData.singer; color: "#FFFFFF"; font.pixelSize: Math.round(32 * tv.u); font.weight: Font.ExtraBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: modelData.combined; color: "#C9C6E8"; font.pixelSize: Math.round(20 * tv.u); font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                    Label { visible: modelData.duet; text: "DUET"; color: "#9A97C0"; font.pixelSize: Math.round(16 * tv.u); font.weight: Font.Bold }
                }
            }
        }
        }

        Label {
            visible: tv.pages > 1
            Layout.alignment: Qt.AlignHCenter
            text: "page " + (tv.page + 1) + " of " + tv.pages
            color: "#6a678f"
            font.pixelSize: Math.round(13 * tv.u)
        }
    }
}

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

    LibraryPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false, result: null})
    property int shownSeq: 0
    Connections {
        target: page.control
        function onViewJsonChanged() {
            page.cview = JSON.parse(page.control.viewJson)
            if (page.cview.result && page.cview.result.seq !== page.shownSeq && page.addPending) { page.shownSeq = page.cview.result.seq; page.addPending = false; addBanner.visible = true; addBannerTimer.restart() }
        }
    }
    property bool addPending: false
    Timer { id: addBannerTimer; interval: 9000; onTriggered: addBanner.visible = false }
    function addSong(row) {
        var singer = singerField.text.trim()
        if (!control || !cview.enabled || singer === "") return
        addPending = true
        control.send("Add " + row.title + " for " + singer, "queue.add_song", JSON.stringify({singer: singer, song_id: row.id, key: keyBox.value}), false)
    }

    property var rows: []
    Connections {
        target: panel
        function onResultsJsonChanged() { page.rows = JSON.parse(panel.resultsJson) }
    }

    Component.onCompleted: { if (arg("--query") !== "") field.text = arg("--query") }
    // test hook: --dump-library FILE writes the rows on screen once the search has answered
    Timer { interval: 2500; running: arg("--dump-library") !== ""; onTriggered: panel.dumpRows(arg("--dump-library")) }
    Timer { id: debounce; interval: 120; onTriggered: panel.search(field.text) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: "SONG LIBRARY"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search artist, title or disc id"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search songs"
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            TextField {
                id: singerField
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                placeholderText: "Add songs for singer (a new name creates a new singer)"
                color: "#e8e6f5"
                placeholderTextColor: "#7a77a0"
                font.pixelSize: 13
                selectByMouse: true
                maximumLength: 60
                background: Rectangle { color: "#12102a"; radius: 8; border.color: singerField.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
                Accessible.name: "Singer to add songs for"
            }
            Label { text: "Key"; color: "#9a97c0"; font.pixelSize: 12 }
            SpinBox {
                id: keyBox
                from: -4; to: 4; value: 0
                implicitWidth: 110
                editable: false
                textFromValue: function(v) { return (v > 0 ? "+" : "") + v }
                Accessible.name: "Key change for the added song"
            }
        }
        Label {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.pixelSize: 12
            visible: !page.cview.enabled
            color: "#7a77a0"
            text: "To add songs to the rotation, connect on the Controls tab (the app must be online)."
        }
        Rectangle {
            id: addBanner
            visible: false
            Layout.fillWidth: true
            height: addBannerText.implicitHeight + 14
            radius: 8
            color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
            border.color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#2f7a4b" : (page.cview.result.kind === "warn" ? "#8a6d1c" : "#a33"))
            Label { id: addBannerText; anchors.fill: parent; anchors.margins: 7; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 13; text: page.cview.result ? page.cview.result.text : "" }
            Accessible.role: Accessible.AlertMessage
            Accessible.name: page.cview.result ? page.cview.result.text : ""
        }

        Label { text: panel.status; color: "#9a97c0"; font.pixelSize: 12 }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.rows
            spacing: 2
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: 48
                radius: 6
                color: ListView.isCurrentItem ? "#4b2bb0" : "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.left + ", " + modelData.right
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Label { text: modelData.artist; color: "#ffffff"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { text: modelData.title; color: "#c9c6e8"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                    }
                    Label { text: modelData.right; color: "#9a97c0"; font.pixelSize: 11 }
                    Button {
                        text: "Add"
                        implicitHeight: 30
                        implicitWidth: 64
                        font.pixelSize: 12
                        enabled: page.cview.enabled && singerField.text.trim() !== ""
                        contentItem: Label { text: parent.text; color: parent.enabled ? "#ffffff" : "#6a678f"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 6; color: !parent.enabled ? "#15132b" : (parent.down ? "#3a2290" : "#4b2bb0") }
                        onClicked: page.addSong(modelData)
                        Accessible.name: "Add " + modelData.title + " for the singer"
                    }
                }
                MouseArea { id: rowArea; anchors.fill: parent; hoverEnabled: true; onClicked: list.currentIndex = index }
                ToolTip.visible: rowArea.containsMouse && modelData.tooltip !== ""
                ToolTip.delay: 600
                ToolTip.text: modelData.tooltip
            }
        }
    }
}

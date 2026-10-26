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

    MusicPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false, bg: {playing: false, volume: 0, index: -1, track: ""}})
    // connected and the app online: the app tells us which track it is on, so rows already played are hidden exactly as in the app
    property bool live: cview.enabled === true
    property int cutoff: live && cview.bg ? cview.bg.index : -1
    onCutoffChanged: panel.refresh(field.text, cutoff)
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    property var rows: JSON.parse(panel.rowsJson)
    Connections { target: panel; function onRowsJsonChanged() { page.rows = JSON.parse(panel.rowsJson) } }

    function act(label, command, args) { if (control) control.send(label, command, JSON.stringify(args || {}), false) }

    Component.onCompleted: { if (arg("--music-query") !== "") field.text = arg("--music-query"); panel.refresh(field.text, cutoff) }
    Timer { id: debounce; interval: 120; onTriggered: panel.refresh(field.text, page.cutoff) }
    // test hook: --dump-music FILE writes the rows on screen once the app has answered
    Timer { interval: 6000; running: page.arg("--dump-music") !== ""; onTriggered: panel.dumpRows(page.arg("--dump-music")) }
    // the app rewrites the playlist when it changes; look again every few seconds while this tab is on screen
    Timer { interval: 4000; running: page.visible; repeat: true; onTriggered: panel.refresh(field.text, page.cutoff) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Label { text: "BACKGROUND MUSIC"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            Label { text: panel.countLabel; color: "#a78bfa"; font.pixelSize: 12; font.weight: Font.DemiBold }
        }
        Label { Layout.fillWidth: true; text: panel.info; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; visible: text !== "" && !page.live }

        // live strip: only while connected to the app
        Rectangle {
            Layout.fillWidth: true
            visible: page.live
            height: liveCol.implicitHeight + 20
            radius: 10
            color: "#12102a"
            border.color: "#2a2650"
            ColumnLayout {
                id: liveCol
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                        color: "#ffffff"
                        text: page.cview.bg && page.cview.bg.track !== "" ? (page.cview.bg.playing ? "Playing: " : "Paused: ") + page.cview.bg.track : "Background music: no playlist"
                        Accessible.name: text
                    }
                    Button {
                        text: "Next"
                        implicitHeight: 32
                        font.pixelSize: 13
                        contentItem: Label { text: parent.text; color: "#ffffff"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 6; color: parent.down ? "#2a2650" : "#1f1c45" }
                        onClicked: page.act("Next track", "bg.next")
                        Accessible.name: "Skip to the next background music track"
                    }
                    Button {
                        text: page.cview.bg && page.cview.bg.playing ? "Pause" : "Play"
                        implicitHeight: 32
                        font.pixelSize: 13
                        contentItem: Label { text: parent.text; color: "#ffffff"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 6; color: parent.down ? "#3a2290" : "#4b2bb0" }
                        onClicked: page.act("Background music", "bg_play_pause")
                        Accessible.name: "Play or pause the background music"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Label { text: "Volume"; color: "#9a97c0"; font.pixelSize: 12; Layout.preferredWidth: 60 }
                    Slider {
                        id: vol
                        Layout.fillWidth: true
                        from: 0; to: 1
                        Binding { target: vol; property: "value"; value: page.cview.bg ? page.cview.bg.volume : 0; when: !vol.pressed }
                        onPressedChanged: if (!pressed) page.act("Music volume " + Math.round(value * 100) + "%", "set_bg_volume", {value: Math.round(value * 100) / 100})
                        onMoved: if (!pressed) page.act("Music volume " + Math.round(value * 100) + "%", "set_bg_volume", {value: Math.round(value * 100) / 100})
                        Accessible.name: "Background music volume"
                    }
                    Label { text: Math.round(vol.value * 100) + "%"; color: "#c9c6e8"; font.pixelSize: 12; Layout.preferredWidth: 44; horizontalAlignment: Text.AlignRight }
                }
            }
        }

        TextField {
            id: field
            Layout.fillWidth: true
            placeholderText: "Search the playlist"
            color: "#e8e6f5"
            placeholderTextColor: "#7a77a0"
            font.pixelSize: 14
            selectByMouse: true
            background: Rectangle { color: "#12102a"; radius: 8; border.color: field.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
            onTextChanged: debounce.restart()
            Accessible.name: "Search the background music playlist"
        }

        Label { text: panel.status; color: "#f0b44c"; font.pixelSize: 12; visible: text !== "" }
        Label { text: page.live ? "Tracks already played are hidden, as in the app." : "Shows every saved track. Connect on the Controls tab to follow the player: played tracks then hide, as in the app."; color: "#7a77a0"; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.Wrap }

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
                height: 34
                radius: 6
                color: "#1a1838"
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.filename
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 6
                    Label { Layout.fillWidth: true; text: modelData.filename; color: "#ffffff"; font.pixelSize: 13; elide: Text.ElideRight }
                    Button {
                        text: "Play"
                        visible: page.live
                        implicitHeight: 26
                        implicitWidth: 56
                        font.pixelSize: 12
                        contentItem: Label { text: parent.text; color: "#ffffff"; font: parent.font; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { radius: 5; color: parent.down ? "#3a2290" : "#4b2bb0" }
                        // the row's own file name rides along, so a playlist that changed since this list was drawn cannot start the wrong song
                        onClicked: page.act("Play " + modelData.filename, "bg.play_track", {index: modelData.index, track: modelData.filename})
                        Accessible.name: "Play " + modelData.filename + " now"
                    }
                }
                ToolTip.visible: hover.hovered && modelData.path !== ""
                ToolTip.text: modelData.path
                HoverHandler { id: hover }
            }
        }
    }
}

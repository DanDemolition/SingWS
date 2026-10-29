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

    HistoryPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false})
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    property bool live: cview.enabled === true
    // the edit form: songKey "" = add a song to the selected singer, otherwise edit that song. The app backs the history up first and refuses while a song plays.
    property string editSongKey: ""
    function openEdit(songKey) {
        editSongKey = songKey
        var f = songKey === "" ? ({artist: "", title: "", disc_id: "", song_type: "", key: 0, tempo_percent: 100}) : JSON.parse(panel.songFields(page.selectedKey, songKey) || "{}")
        fArtist.text = f.artist || ""; fTitle.text = f.title || ""; fDisc.text = f.disc_id || ""; fType.text = f.song_type || ""
        fKey.value = f.key === undefined ? 0 : f.key; fTempo.value = f.tempo_percent === undefined ? 100 : f.tempo_percent
        editDialog.open()
    }
    Dialog {
        id: editDialog
        title: page.editSongKey === "" ? "Add song to " + panel.singerName : "Edit song for " + panel.singerName
        modal: true
        anchors.centerIn: parent
        width: Math.min(page.width - 40, 460)
        standardButtons: Dialog.Save | Dialog.Cancel
        Overlay.modal: Rectangle { color: "#aa000000" }
        background: Rectangle { color: "#1a1838"; radius: 10; border.color: "#2a2650" }
        header: Label { text: editDialog.title; color: "#ffffff"; font.pixelSize: 15; font.weight: Font.DemiBold; padding: 14 }
        onAccepted: {
            if (!control || fTitle.text.trim() === "") return
            var args = {singer_key: page.selectedKey, artist: fArtist.text, title: fTitle.text, disc_id: fDisc.text, song_type: fType.text, key: fKey.value, tempo_percent: fTempo.value}
            if (page.editSongKey !== "") args.song_key = page.editSongKey
            control.send(page.editSongKey === "" ? "Add song" : "Edit song", page.editSongKey === "" ? "history.add_song" : "history.edit_song", JSON.stringify(args), false)
        }
        contentItem: ColumnLayout {
            spacing: 6
            Label { text: "The history is backed up before it changes. Not allowed while a song plays."; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Label { text: "Artist"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: fArtist; Layout.fillWidth: true; maximumLength: 100; selectByMouse: true; Accessible.name: "Artist" }
            Label { text: "Title"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: fTitle; Layout.fillWidth: true; maximumLength: 100; selectByMouse: true; Accessible.name: "Title (required)" }
            Label { text: "Disc ID"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: fDisc; Layout.fillWidth: true; maximumLength: 40; selectByMouse: true; Accessible.name: "Disc ID" }
            Label { text: "Song type (CDG, ZIP, MP4)"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: fType; Layout.fillWidth: true; maximumLength: 10; selectByMouse: true; Accessible.name: "Song type" }
            RowLayout {
                Label { text: "Key"; color: "#c9c6e8"; font.pixelSize: 12 }
                SpinBox { id: fKey; from: -24; to: 24; editable: true; Accessible.name: "Key, -24 to 24" }
                Label { text: "Tempo %"; color: "#c9c6e8"; font.pixelSize: 12 }
                SpinBox { id: fTempo; from: 50; to: 200; editable: true; Accessible.name: "Tempo percent, 50 to 200" }
            }
        }
    }

    property var singers: []
    property var songs: []
    property string selectedKey: ""

    Connections {
        target: panel
        function onSingersJsonChanged() {
            page.singers = JSON.parse(panel.singersJson)
            // keep the selection when the singer is still listed; otherwise select the first real singer
            var keep = -1, first = -1
            for (var i = 0; i < page.singers.length; i++) {
                if (!page.singers[i].selectable) continue
                if (first < 0) first = i
                if (page.singers[i].key === page.selectedKey) keep = i
            }
            var pick = keep >= 0 ? keep : first
            if (pick >= 0) { singerList.currentIndex = pick; page.choose(page.singers[pick].key) }
            else { singerList.currentIndex = -1; page.choose("") }
        }
        function onSongsJsonChanged() { page.songs = JSON.parse(panel.songsJson) }
    }

    function choose(key) {
        selectedKey = key
        panel.selectSinger(key, songField.text)
    }

    Component.onCompleted: {
        if (arg("--singer-search") !== "") singerField.text = arg("--singer-search")
        if (arg("--song-query") !== "") songField.text = arg("--song-query")
        panel.refresh(singerField.text)
    }
    Timer { id: singerDebounce; interval: 120; onTriggered: panel.refresh(singerField.text) }
    Timer { id: songDebounce; interval: 120; onTriggered: panel.selectSinger(page.selectedKey, songField.text) }

    RowLayout {
        anchors.fill: parent
        spacing: 14

        ColumnLayout {
            Layout.preferredWidth: parent.width * 0.4
            Layout.fillHeight: true
            spacing: 8
            Label { text: "SINGER HISTORY"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
            TextField {
                id: singerField
                Layout.fillWidth: true
                placeholderText: "Search singers or songs"
                color: "#e8e6f5"; placeholderTextColor: "#7a77a0"; font.pixelSize: 14; selectByMouse: true
                background: Rectangle { color: "#12102a"; radius: 8; border.color: singerField.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
                onTextChanged: singerDebounce.restart()
                Accessible.name: "Search singers"
            }
            Label { text: panel.status; color: "#9a97c0"; font.pixelSize: 12 }
            ListView {
                id: singerList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 2
                model: page.singers
                currentIndex: -1
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    width: singerList.width
                    height: col.implicitHeight + 14
                    radius: 6
                    color: !modelData.selectable ? "transparent" : (ListView.isCurrentItem ? "#4b2bb0" : "#1a1838")
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.title + ", " + modelData.detail.replace("\n", ", ")
                    ColumnLayout {
                        id: col
                        anchors.fill: parent
                        anchors.margins: 7
                        spacing: 0
                        Label { text: modelData.title; color: modelData.selectable ? "#ffffff" : "#b4b8c6"; font.pixelSize: 14; font.weight: Font.DemiBold; wrapMode: Text.Wrap; Layout.fillWidth: true }
                        Label { visible: modelData.detail !== ""; text: modelData.detail; color: "#c9c6e8"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                    MouseArea {
                        anchors.fill: parent
                        enabled: modelData.selectable
                        onClicked: { singerList.currentIndex = index; page.choose(modelData.key) }
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Label { text: panel.singerName; color: "#ffffff"; font.pixelSize: 18; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                Button { visible: page.live && page.selectedKey !== ""; text: "Add song"; implicitHeight: 28; onClicked: page.openEdit(""); Accessible.name: "Add a song to this singer's history" }
            }
            Rectangle {
                Layout.fillWidth: true
                visible: page.live && page.cview.result !== undefined && page.cview.result !== null
                height: visible ? htext.implicitHeight + 12 : 0
                radius: 6
                color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
                Label { id: htext; anchors.fill: parent; anchors.margins: 6; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 12; text: page.cview.result ? page.cview.result.text : "" }
                Accessible.role: Accessible.StaticText
                Accessible.name: page.cview.result ? page.cview.result.text : ""
            }
            Label { text: panel.summary; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap; Layout.fillWidth: true }
            TextField {
                id: songField
                Layout.fillWidth: true
                placeholderText: "Search this singer's songs"
                color: "#e8e6f5"; placeholderTextColor: "#7a77a0"; font.pixelSize: 14; selectByMouse: true
                background: Rectangle { color: "#12102a"; radius: 8; border.color: songField.activeFocus ? "#7c3aed" : "#2a2650"; border.width: 1 }
                onTextChanged: songDebounce.restart()
                Accessible.name: "Search this singer's songs"
            }
            Label { text: panel.songMeta; color: "#9a97c0"; font.pixelSize: 12 }
            ListView {
                id: songList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 2
                model: page.songs
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    width: songList.width
                    height: Math.max(scol.implicitHeight + 12, page.live && modelData.selectable ? 40 : 0)
                    radius: 6
                    color: modelData.selectable ? "#1a1838" : "transparent"
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.title + ", " + modelData.detail
                    Button {
                        id: editBtn
                        visible: page.live && modelData.selectable
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Edit"
                        implicitHeight: 28
                        onClicked: page.openEdit(modelData.key)
                        Accessible.name: "Edit " + modelData.title
                    }
                    ColumnLayout {
                        id: scol
                        anchors.fill: parent
                        anchors.rightMargin: editBtn.visible ? editBtn.width + 16 : 6
                        anchors.leftMargin: 6
                        anchors.topMargin: 6
                        anchors.bottomMargin: 6
                        spacing: 0
                        Label { text: modelData.title; color: modelData.selectable ? "#ffffff" : "#b4b8c6"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { visible: modelData.detail !== ""; text: modelData.detail; color: "#c9c6e8"; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
        }
    }
}

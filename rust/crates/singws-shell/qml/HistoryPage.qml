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
            Label { text: panel.singerName; color: "#ffffff"; font.pixelSize: 18; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
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
                    height: scol.implicitHeight + 12
                    radius: 6
                    color: modelData.selectable ? "#1a1838" : "transparent"
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.title + ", " + modelData.detail
                    ColumnLayout {
                        id: scol
                        anchors.fill: parent
                        anchors.margins: 6
                        spacing: 0
                        Label { text: modelData.title; color: modelData.selectable ? "#ffffff" : "#b4b8c6"; font.pixelSize: 14; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                        Label { visible: modelData.detail !== ""; text: modelData.detail; color: "#c9c6e8"; font.pixelSize: 11; wrapMode: Text.Wrap; Layout.fillWidth: true }
                    }
                }
            }
        }
    }
}

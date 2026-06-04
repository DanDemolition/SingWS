import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Card {
    id: panel
    property var bridge
    property var songModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 9

        Text {
            text: "SONG LIBRARY"
            color: Theme.text
            font.pixelSize: 15
            font.weight: Font.Bold
            Layout.fillWidth: true
        }

        TextField {
            id: searchField
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            placeholderText: "Search songs, artists, or titles"
            color: Theme.text
            placeholderTextColor: Theme.textSoft
            selectedTextColor: "white"
            selectionColor: Theme.accent
            onAccepted: if (panel.bridge) panel.bridge.search(text)
            onTextEdited: searchDebounce.restart()

            Timer {
                id: searchDebounce
                interval: 220
                repeat: false
                onTriggered: if (panel.bridge) panel.bridge.search(searchField.text)
            }

            background: Rectangle {
                radius: Theme.radius
                color: Theme.panelDeep
                border.color: searchField.activeFocus ? Theme.accentBright : Theme.borderSoft
            }
        }

        ListView {
            id: songList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: panel.songModel || []
            spacing: 2

            delegate: Rectangle {
                id: songDelegate
                required property int index
                required property string title
                required property string artist
                required property string brand
                required property string duration
                required property string fileType

                width: songList.width
                height: 52
                radius: 6
                color: ListView.isCurrentItem
                    ? Qt.rgba(0.42, 0.16, 1.0, 0.54)
                    : (songDelegate.index % 2 === 1 ? Theme.rowAlt : "transparent")
                border.color: ListView.isCurrentItem ? Qt.rgba(0.63, 0.13, 1.0, 0.52) : Qt.rgba(1, 1, 1, 0.025)

                ColumnLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    anchors.topMargin: 7
                    anchors.bottomMargin: 7
                    spacing: 2

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        Text {
                            text: songDelegate.title
                            color: Theme.text
                            elide: Text.ElideRight
                            font.pixelSize: 13
                            Layout.fillWidth: true
                        }
                        Text {
                            text: songDelegate.artist
                            color: Theme.textMuted
                            elide: Text.ElideRight
                            font.pixelSize: 12
                            Layout.preferredWidth: 100
                        }
                        Text {
                            text: songDelegate.brand
                            color: Theme.textSoft
                            elide: Text.ElideRight
                            font.pixelSize: 11
                            Layout.preferredWidth: 54
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            text: songDelegate.fileType
                            color: Theme.accent
                            font.pixelSize: 10
                            font.weight: Font.Bold
                            visible: songDelegate.fileType !== ""
                        }
                        Text {
                            text: songDelegate.duration
                            color: Theme.textSoft
                            font.pixelSize: 10
                            visible: songDelegate.duration !== ""
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: songList.currentIndex = songDelegate.index
                }
            }
        }

        Text {
            text: songList.count + " songs"
            color: Theme.textMuted
            font.pixelSize: 12
        }
    }
}

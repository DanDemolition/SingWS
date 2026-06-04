import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Card {
    id: panel
    property var bridge
    property var bgmModel

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 9

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "BACKGROUND MUSIC"
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.Bold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: bgmList.count + " tracks"
                color: Theme.textMuted
                font.pixelSize: 12
            }
        }

        TextField {
            id: bgmFilter
            Layout.fillWidth: true
            Layout.preferredHeight: 42
            placeholderText: "Filter background music"
            color: Theme.text
            placeholderTextColor: Theme.textSoft
            selectedTextColor: "white"
            selectionColor: Theme.accent

            background: Rectangle {
                radius: Theme.radius
                color: Theme.panelDeep
                border.color: bgmFilter.activeFocus ? Theme.accentBright : Theme.borderSoft
            }
        }

        ListView {
            id: bgmList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: panel.bgmModel || []
            spacing: 2

            delegate: Rectangle {
                id: bgmDelegate
                required property int index
                required property string title
                required property string fileType
                required property bool isCurrent

                width: bgmList.width
                height: 52
                radius: 6
                color: bgmDelegate.isCurrent
                    ? Qt.rgba(0.42, 0.16, 1.0, 0.54)
                    : (ListView.isCurrentItem
                        ? Qt.rgba(0.42, 0.16, 1.0, 0.34)
                        : (bgmDelegate.index % 2 === 1 ? Theme.rowAlt : "transparent"))
                border.color: bgmDelegate.isCurrent
                    ? Qt.rgba(0.63, 0.13, 1.0, 0.52)
                    : (ListView.isCurrentItem ? Qt.rgba(0.63, 0.13, 1.0, 0.36) : Qt.rgba(1, 1, 1, 0.025))

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
                            text: bgmDelegate.isCurrent ? "▶" : ""
                            color: Theme.accentBright
                            font.pixelSize: 12
                            font.weight: Font.Bold
                            visible: bgmDelegate.isCurrent
                            Layout.preferredWidth: visible ? 14 : 0
                        }
                        Text {
                            text: bgmDelegate.title
                            color: Theme.text
                            elide: Text.ElideRight
                            font.pixelSize: 13
                            font.weight: bgmDelegate.isCurrent ? Font.DemiBold : Font.Normal
                            Layout.fillWidth: true
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Text {
                            text: bgmDelegate.fileType
                            color: Theme.accent
                            font.pixelSize: 10
                            font.weight: Font.Bold
                            visible: bgmDelegate.fileType !== ""
                        }
                        Text {
                            text: bgmDelegate.isCurrent ? "NOW PLAYING" : ""
                            color: Theme.textSoft
                            font.pixelSize: 10
                            visible: bgmDelegate.isCurrent
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                MouseArea {
                    anchors.fill: parent
                    onClicked: bgmList.currentIndex = bgmDelegate.index
                }
            }
        }
    }
}

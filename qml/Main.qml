import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1500
    height: 930
    minimumWidth: 1180
    minimumHeight: 760
    visible: true
    title: "SingWS"
    color: Theme.canvas

    AppShell {
        anchors.fill: parent
        bridge: singws
        songModel: songLibraryModel
        rotationQueueModel: rotationModel
        bgmListModel: bgmModel
        transport: transportState
    }
}

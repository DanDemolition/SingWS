import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    WaitlistPanel { id: panel }
    property var control: null   // the shared ControlPanel (Main.qml); null when the page is used on its own
    property var cview: control ? JSON.parse(control.viewJson) : ({enabled: false})
    Connections { target: page.control; function onViewJsonChanged() { page.cview = JSON.parse(page.control.viewJson) } }
    // connected to the app: the buttons work; otherwise the page stays read-only
    property bool live: cview.enabled === true
    function act(label, command, requestId) { if (control) control.send(label, command, JSON.stringify({request_id: requestId}), false) }
    // Edit changes singer / song / key / tempo of the SAME request (its id and place never change); the app tells the server
    property int editId: 0
    function openEdit(row) {
        editId = row.request_id
        eSinger.text = row.singer || ""; eArtist.text = row.artist || ""; eTitle.text = row.title || ""
        eKey.value = row.key || 0; eTempo.value = row.tempo || 0
        editDialog.open()
    }
    Dialog {
        id: editDialog
        title: "Edit waitlist request"
        modal: true
        anchors.centerIn: parent
        width: Math.min(page.width - 40, 440)
        standardButtons: Dialog.Save | Dialog.Cancel
        Overlay.modal: Rectangle { color: "#aa000000" }
        background: Rectangle { color: "#1a1838"; radius: 10; border.color: "#2a2650" }
        header: Label { text: editDialog.title; color: "#ffffff"; font.pixelSize: 15; font.weight: Font.DemiBold; padding: 14 }
        onAccepted: {
            if (!control || eSinger.text.trim() === "" || eTitle.text.trim() === "") return
            control.send("Edit " + eSinger.text, "waitlist.edit", JSON.stringify({request_id: page.editId, singer: eSinger.text, artist: eArtist.text, title: eTitle.text, key: eKey.value, tempo: eTempo.value}), false)
        }
        contentItem: ColumnLayout {
            spacing: 6
            Label { text: "Singer"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: eSinger; Layout.fillWidth: true; maximumLength: 60; selectByMouse: true; Accessible.name: "Singer (required)" }
            Label { text: "Song title"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: eTitle; Layout.fillWidth: true; maximumLength: 100; selectByMouse: true; Accessible.name: "Song title (required)" }
            Label { text: "Artist"; color: "#c9c6e8"; font.pixelSize: 12 }
            TextField { id: eArtist; Layout.fillWidth: true; maximumLength: 100; selectByMouse: true; Accessible.name: "Artist" }
            RowLayout {
                Label { text: "Key"; color: "#c9c6e8"; font.pixelSize: 12 }
                SpinBox { id: eKey; from: -12; to: 12; editable: true; Accessible.name: "Key, -12 to 12" }
                Label { text: "Tempo"; color: "#c9c6e8"; font.pixelSize: 12 }
                SpinBox { id: eTempo; from: -30; to: 30; editable: true; Accessible.name: "Tempo change, -30 to 30" }
            }
        }
    }
    property var sections: JSON.parse(panel.sectionsJson)
    // the flat list the ListView draws: a header per section, then its rows
    property var items: {
        var out = []
        for (var s = 0; s < sections.length; s++) {
            out.push({ header: true, title: sections[s].title + " (" + sections[s].rows.length + ")", helper: sections[s].helper || "", key: sections[s].key })
            for (var r = 0; r < sections[s].rows.length; r++) out.push({ header: false, row: sections[s].rows[r], key: sections[s].key })
        }
        return out
    }
    Connections { target: panel; function onSectionsJsonChanged() { page.sections = JSON.parse(panel.sectionsJson) } }

    Component.onCompleted: panel.refresh()
    Timer { interval: 3000; running: page.visible; repeat: true; onTriggered: panel.refresh() }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        Label { text: page.live ? "WAITLIST" : "WAITLIST (read-only: not connected to the app)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap }
        // what the app answered to the last button
        Rectangle {
            Layout.fillWidth: true
            visible: page.live && page.cview.result !== undefined && page.cview.result !== null
            height: visible ? resultText.implicitHeight + 14 : 0
            radius: 6
            color: !page.cview.result ? "transparent" : (page.cview.result.kind === "ok" ? "#12301f" : (page.cview.result.kind === "warn" ? "#33290f" : "#3a1414"))
            Label { id: resultText; anchors.fill: parent; anchors.margins: 7; wrapMode: Text.Wrap; color: "#ffffff"; font.pixelSize: 13; text: page.cview.result ? page.cview.result.text : "" }
            Accessible.role: Accessible.StaticText
            Accessible.name: page.cview.result ? page.cview.result.text : ""
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: page.items
            spacing: 3
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                width: list.width
                height: modelData.header ? (modelData.helper ? 54 : 34) : Math.max(rowText.implicitHeight + 16, page.live && !modelData.header && (modelData.key === "waitlist" || modelData.key === "removed") ? 44 : 0)
                radius: 6
                color: modelData.header ? (modelData.key === "pending_acceptance" ? "#0e3a52" : modelData.key === "removed" ? "#4a1d1d" : "#1f2540")
                                        : ((modelData.row && modelData.row.first_time_singer) ? "#16341f" : "#1a1838")
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.header ? modelData.title : (modelData.row ? modelData.row.text : "")
                Column {
                    anchors.fill: parent
                    anchors.margins: 8
                    visible: modelData.header
                    Label { text: modelData.title || ""; color: "#e8e6f5"; font.pixelSize: 14; font.weight: Font.DemiBold }
                    Label { visible: !!modelData.helper; text: modelData.helper || ""; color: "#9a97c0"; font.pixelSize: 12; width: parent.width; elide: Text.ElideRight }
                }
                // host actions: Add / Remove on the waitlist, Restore on what was removed tonight (pending adds are added by the app itself)
                Row {
                    id: buttons
                    visible: page.live && !modelData.header && (modelData.key === "waitlist" || modelData.key === "removed")
                    anchors.right: parent.right
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    property bool sure: false
                    Timer { id: sureTimer; interval: 3000; onTriggered: buttons.sure = false }
                    Button {
                        visible: modelData.key === "waitlist"
                        text: "Edit"
                        implicitHeight: 30
                        onClicked: page.openEdit(modelData.row)
                        Accessible.name: "Edit " + (modelData.row ? modelData.row.singer : "") + "'s request"
                    }
                    Button {
                        visible: modelData.key === "waitlist"
                        text: "Add"
                        implicitHeight: 30
                        onClicked: page.act("Add " + modelData.row.singer, "waitlist.add", modelData.row.request_id)
                        Accessible.name: "Add " + (modelData.row ? modelData.row.singer : "") + " to the rotation"
                    }
                    Button {
                        visible: modelData.key === "waitlist"
                        text: buttons.sure ? "Sure?" : "Remove"
                        implicitHeight: 30
                        onClicked: {
                            if (!buttons.sure) { buttons.sure = true; sureTimer.restart(); return }
                            buttons.sure = false
                            page.act("Remove " + modelData.row.singer, "waitlist.remove", modelData.row.request_id)
                        }
                        Accessible.name: (buttons.sure ? "Confirm removing " : "Remove ") + (modelData.row ? modelData.row.singer : "") + " from the waitlist"
                    }
                    Button {
                        visible: modelData.key === "removed"
                        text: "Restore"
                        implicitHeight: 30
                        onClicked: page.act("Restore " + modelData.row.singer, "waitlist.restore", modelData.row.request_id)
                        Accessible.name: "Restore " + (modelData.row ? modelData.row.singer : "") + " to the waitlist"
                    }
                }
                Label {
                    id: rowText
                    visible: !modelData.header
                    anchors.fill: parent
                    anchors.margins: 8
                    anchors.rightMargin: buttons.visible ? buttons.width + 16 : 8
                    text: (modelData.header || !modelData.row) ? "" : modelData.row.text
                    color: "#ffffff"
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}

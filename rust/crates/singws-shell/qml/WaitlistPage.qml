import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import com.singws.shell

Item {
    id: page

    WaitlistPanel { id: panel }
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

        Label { text: "WAITLIST (read-only)"; color: "#e8e6f5"; font.pixelSize: 15; font.weight: Font.DemiBold }
        Label { Layout.fillWidth: true; text: panel.status; color: "#9a97c0"; font.pixelSize: 12; wrapMode: Text.Wrap }

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
                height: modelData.header ? (modelData.helper ? 54 : 34) : rowText.implicitHeight + 16
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
                Label {
                    id: rowText
                    visible: !modelData.header
                    anchors.fill: parent
                    anchors.margins: 8
                    text: (modelData.header || !modelData.row) ? "" : modelData.row.text
                    color: "#ffffff"
                    font.pixelSize: 13
                    wrapMode: Text.Wrap
                }
            }
        }
    }
}

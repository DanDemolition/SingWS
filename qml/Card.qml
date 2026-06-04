import QtQuick

Rectangle {
    id: card
    color: Theme.panel
    radius: Theme.radius
    border.color: Theme.borderSoft
    border.width: 1

    gradient: Gradient {
        GradientStop { position: 0.0; color: "#081522" }
        GradientStop { position: 0.58; color: "#06101B" }
        GradientStop { position: 1.0; color: "#02060D" }
    }
}

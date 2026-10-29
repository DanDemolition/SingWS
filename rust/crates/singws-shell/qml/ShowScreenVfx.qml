import QtQuick

Rectangle {
    id: root
    color: "transparent"
    clip: true
    property bool effectsEnabled: true
    property bool active: false
    property string singerText: ""
    property string songText: ""
    property string artistText: ""
    property string onDeckText: ""
    property int countdownValue: 0
    property int startCountdownValue: 0
    property bool startCountdownActive: false
    property int burstSerial: 0
    property string transitionStyle: "moving_spotlights"
    readonly property bool burstStyle: transitionStyle === "confetti_drop"
    signal nextUpCountdownFinished()

    function showNextUp(singer, song, artist, onDeck, durationSeconds) {
        singerCountdownTimer.stop()
        startCountdownActive = false
        singerText = singer || ""
        songText = song || ""
        artistText = artist || ""
        onDeckText = onDeck || ""
        countdownValue = Math.max(1, Math.ceil(durationSeconds || 10))
        active = true
        nowPanel.opacity = 0
        nextPanel.opacity = 0
        nextPanel.scale = 0.66
        nextPanel.y = (root.height - nextPanel.height) / 2
        backdrop.opacity = 0
        countdownTimer.restart()
        nextUpEntrance.restart()
    }

    function showSingerStart(singer, song, artist, style) {
        // A previous outro/Next Up exit may still be finishing when the next
        // singer is started. Cancel those writers before resetting the shared
        // overlay state, or their final ScriptAction can hide this countdown.
        overlayExit.stop()
        songOutroSequence.stop()
        nextUpEntrance.stop()
        singerStartSequence.stop()
        startCountdownEntrance.stop()
        countdownNumberHit.stop()
        singerText = singer || ""
        songText = song || ""
        artistText = artist || ""
        transitionStyle = style || "moving_spotlights"
        countdownTimer.stop()
        singerCountdownTimer.stop()
        active = true
        nextPanel.opacity = 0
        nowPanel.opacity = 0
        nowPanel.scale = 0.40
        backdrop.opacity = 0
        startCountdownValue = 3
        startCountdownActive = true
        startCountdownStage.opacity = 0
        countdownNumber.scale = 0.25
        countdownRing.opacity = 0
        startCountdownEntrance.restart()
        countdownNumberHit.restart()
        singerCountdownTimer.restart()
    }

    function showSongOutro(singer, song, artist, style) {
        singerText = singer || ""
        songText = song || ""
        artistText = artist || ""
        transitionStyle = style || "moving_spotlights"
        countdownTimer.stop()
        singerCountdownTimer.stop()
        startCountdownActive = false
        active = true
        nextPanel.opacity = 0
        nowPanel.opacity = 0
        outroPanel.opacity = 0
        outroPanel.scale = 0.36
        backdrop.opacity = 0
        flash.opacity = 0
        shockwave.opacity = 0
        burstSerial += 1
        songOutroSequence.restart()
    }

    function hideTransition(immediate) {
        countdownTimer.stop()
        singerCountdownTimer.stop()
        // Every transition below writes to one or more of the shared layers.
        // Stopping only the timers lets an in-flight singer/outro animation
        // restore opacity after the exit has hidden it, leaving a name card
        // stuck until the Quick surface is recreated.
        nextUpEntrance.stop()
        countdownHit.stop()
        startCountdownEntrance.stop()
        countdownNumberHit.stop()
        singerStartSequence.stop()
        songOutroSequence.stop()
        overlayExit.stop()
        startCountdownActive = false
        if (immediate) {
            nextPanel.opacity = 0
            nowPanel.opacity = 0
            outroPanel.opacity = 0
            startCountdownStage.opacity = 0
            countdownNumber.opacity = 0
            countdownRing.opacity = 0
            backdrop.opacity = 0
            flash.opacity = 0
            shockwave.opacity = 0
            active = false
        } else if (active) {
            overlayExit.restart()
        }
    }

    Timer {
        id: countdownTimer
        interval: 1000
        repeat: true
        onTriggered: {
            if (root.countdownValue > 1) {
                root.countdownValue -= 1
                countdownHit.restart()
            } else {
                stop()
                root.countdownValue = 0
                overlayExit.restart()
                root.active = false
                root.nextUpCountdownFinished()
            }
        }
    }

    Timer {
        id: singerCountdownTimer
        // A brisk 3-2-1 keeps the singer-facing cue useful without adding
        // three seconds of dead air. The transport uses the same 2100ms gate.
        interval: 700
        repeat: true
        onTriggered: {
            if (root.startCountdownValue > 1) {
                root.startCountdownValue -= 1
                countdownNumberHit.restart()
            } else {
                stop()
                root.startCountdownValue = 0
                root.startCountdownActive = false
                root.burstSerial += 1
                singerStartSequence.restart()
            }
        }
    }

    Rectangle {
        id: backdrop
        anchors.fill: parent
        opacity: 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#e8060610" }
            GradientStop { position: 0.36; color: "#e8221742" }
            GradientStop { position: 0.68; color: "#e8141028" }
            GradientStop { position: 1.0; color: "#e8060610" }
        }
    }

    Item {
        id: spotlights
        anchors.fill: parent
        z: 22
        visible: root.active && root.effectsEnabled && root.transitionStyle === "moving_spotlights"
        opacity: 0.42
        clip: true
        Repeater {
            model: 3
            Rectangle {
                id: beam
                required property int index
                width: Math.max(130, root.width * 0.18)
                height: root.height * 1.35
                x: root.width * (0.16 + index * 0.34) - width / 2
                y: -root.height * 0.10
                rotation: index === 0 ? -15 : (index === 1 ? 0 : 15)
                transformOrigin: Item.Top
                opacity: index === 1 ? 0.25 : 0.20
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#00ffffff" }
                    GradientStop { position: 0.38; color: index === 1 ? "#30f6c945" : "#28a895ff" }
                    GradientStop { position: 0.5; color: index === 1 ? "#a8f6c945" : "#98b9adff" }
                    GradientStop { position: 0.62; color: index === 1 ? "#30f6c945" : "#28a895ff" }
                    GradientStop { position: 1.0; color: "#00ffffff" }
                }
                SequentialAnimation {
                    running: root.active && root.effectsEnabled
                    loops: Animation.Infinite
                    RotationAnimator {
                        target: beam
                        from: beam.index === 0 ? -19 : (beam.index === 1 ? -8 : 11)
                        to: beam.index === 0 ? -7 : (beam.index === 1 ? 8 : 19)
                        duration: 4600 + beam.index * 650
                        easing.type: Easing.InOutSine
                    }
                    RotationAnimator {
                        target: beam
                        from: beam.index === 0 ? -7 : (beam.index === 1 ? 8 : 19)
                        to: beam.index === 0 ? -19 : (beam.index === 1 ? -8 : 11)
                        duration: 4600 + beam.index * 650
                        easing.type: Easing.InOutSine
                    }
                }
            }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.max(18, root.height * 0.04)
            spacing: Math.max(50, root.width * 0.13)
            Repeater {
                model: 3
                Rectangle {
                    id: floorPool
                    required property int index
                    width: Math.max(90, root.width * 0.13)
                    height: Math.max(14, root.height * 0.026)
                    radius: height / 2
                    color: index === 1 ? "#45f6c945" : "#36a895ff"
                    opacity: 0.30
                    SequentialAnimation {
                        running: root.active && root.effectsEnabled
                        loops: Animation.Infinite
                        OpacityAnimator { target: floorPool; from: 0.18; to: 0.38; duration: 1800 + index * 280; easing.type: Easing.InOutSine }
                        OpacityAnimator { target: floorPool; from: 0.38; to: 0.18; duration: 1800 + index * 280; easing.type: Easing.InOutSine }
                    }
                }
            }
        }
    }

    // Every shuffled look stays inside this one pre-existing Quick surface.
    // The ticker is a later-created sibling below VideoWindow's video area, so
    // none of these styles can create or cover its native child surface.
    Item {
        id: styleLayer
        anchors.fill: parent
        // Keep shuffled artwork immediately beneath the singer/countdown
        // typography. It used to sit at z=8 behind a 95%-opaque z=24 panel,
        // so logs changed style while every audience transition looked alike.
        z: 22
        visible: root.active && root.effectsEnabled
        clip: true

        Item {
            anchors.fill: parent
            visible: root.transitionStyle === "curtain_reveal"
            Rectangle {
                id: curtainLeft
                width: parent.width * 0.51; height: parent.height
                color: "#d12c70"
                gradient: Gradient { GradientStop { position: 0; color: "#2b0739" } GradientStop { position: 1; color: "#ef3f82" } }
                SequentialAnimation on x { running: curtainLeft.visible; loops: Animation.Infinite
                    NumberAnimation { from: 0; to: -curtainLeft.width * 0.96; duration: 720; easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: 900 }
                    NumberAnimation { to: 0; duration: 620; easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: 250 }
                }
            }
            Rectangle {
                id: curtainRight
                width: parent.width * 0.51; height: parent.height; x: parent.width - width
                color: "#7e174f"
                SequentialAnimation on x { running: curtainRight.visible; loops: Animation.Infinite
                    NumberAnimation { from: root.width - curtainRight.width; to: root.width - curtainRight.width * 0.04; duration: 720; easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: 900 }
                    NumberAnimation { to: root.width - curtainRight.width; duration: 620; easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: 250 }
                }
            }
        }

        Repeater {
            model: root.transitionStyle === "neon_scan" ? 7 : 0
            Rectangle {
                required property int index
                width: root.width * 1.3; height: 3; x: -root.width * 0.15
                color: index % 2 ? "#ff42ae" : "#45eaff"; opacity: 0.72; rotation: -8 + index * 2
                SequentialAnimation on y { running: root.active; loops: Animation.Infinite
                    PauseAnimation { duration: index * 90 }
                    NumberAnimation { from: -20; to: root.height + 20; duration: 1150 + index * 80; easing.type: Easing.InOutSine }
                }
            }
        }

        Item {
            anchors.fill: parent; visible: root.transitionStyle === "spinning_record"
            Rectangle {
                id: recordDisc; width: Math.min(root.width, root.height) * 0.68; height: width; radius: width / 2
                x: -width * 0.18; anchors.verticalCenter: parent.verticalCenter; color: "#17131c"
                border.width: Math.max(4, width * 0.045); border.color: "#332b3a"
                Rectangle { anchors.centerIn: parent; width: parent.width * 0.28; height: width; radius: width / 2; color: "#ff4f9a"; border.width: 6; border.color: "#f6c945" }
                RotationAnimator on rotation { running: recordDisc.visible; from: 0; to: 360; duration: 2600; loops: Animation.Infinite }
            }
        }

        Repeater {
            model: root.transitionStyle === "audio_pulse" ? 24 : 0
            Rectangle {
                required property int index
                width: Math.max(5, root.width * 0.009); height: root.height * (0.12 + (index % 7) * 0.055)
                x: root.width * 0.20 + index * root.width * 0.026; y: root.height - height
                radius: width / 2; color: index % 2 ? "#ff4f9a" : "#a36cff"; opacity: 0.48
                SequentialAnimation on scale { running: root.active; loops: Animation.Infinite
                    NumberAnimation { from: 0.25; to: 1.0; duration: 260 + index * 17; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 0.25; duration: 300 + index * 13; easing.type: Easing.InOutSine }
                }
            }
        }

        Rectangle {
            id: irisRing; visible: root.transitionStyle === "camera_iris"; anchors.centerIn: parent
            width: Math.min(root.width, root.height) * 0.72; height: width; radius: width / 2
            color: "transparent"; border.width: width * 0.42; border.color: "#e805030a"
            SequentialAnimation on scale { running: irisRing.visible; loops: Animation.Infinite
                NumberAnimation { from: 0.18; to: 2.8; duration: 900; easing.type: Easing.InOutCubic }
                PauseAnimation { duration: 900 }
                NumberAnimation { to: 0.18; duration: 650; easing.type: Easing.InOutCubic }
            }
        }

        Rectangle {
            id: marqueeFrame; visible: root.transitionStyle === "marquee_lightbulbs"
            anchors.fill: parent; anchors.margins: Math.max(24, root.height * 0.06); radius: 28
            color: "transparent"; border.width: 9; border.color: "#f6c945"
            SequentialAnimation on opacity { running: marqueeFrame.visible; loops: Animation.Infinite
                NumberAnimation { from: 0.28; to: 1.0; duration: 320 }
                NumberAnimation { to: 0.45; duration: 320 }
            }
        }

        Repeater {
            model: root.transitionStyle === "color_ribbon_wipe" ? 5 : 0
            Rectangle {
                required property int index
                width: root.width * 1.45; height: root.height * 0.075; x: -width; y: root.height * (0.22 + index * 0.12)
                rotation: -8; radius: height / 2; opacity: 0.72
                color: index % 3 === 0 ? "#42e7ff" : (index % 3 === 1 ? "#a36cff" : "#ff4f9a")
                SequentialAnimation on x { running: root.active; loops: Animation.Infinite
                    PauseAnimation { duration: index * 90 }
                    NumberAnimation { from: -width; to: root.width; duration: 1250; easing.type: Easing.InOutCubic }
                    PauseAnimation { duration: 600 }
                }
            }
        }

        Repeater {
            model: root.transitionStyle === "star_tunnel" ? 32 : 0
            Rectangle {
                id: starParticle
                required property int index
                readonly property real angle: index * 0.79
                width: 4 + index % 3; height: width; radius: width / 2; color: "#ffffff"
                x: root.width / 2 + Math.cos(angle) * 12; y: root.height / 2 + Math.sin(angle) * 12; opacity: 0.9
                ParallelAnimation { running: root.active; loops: Animation.Infinite
                    XAnimator { target: starParticle; from: root.width / 2; to: root.width / 2 + Math.cos(starParticle.angle) * root.width * 0.65; duration: 850 + index * 24 }
                    YAnimator { target: starParticle; from: root.height / 2; to: root.height / 2 + Math.sin(starParticle.angle) * root.height * 0.75; duration: 850 + index * 24 }
                    OpacityAnimator { target: starParticle; from: 1; to: 0; duration: 850 + index * 24 }
                }
            }
        }

        Grid {
            visible: root.transitionStyle === "mosaic_tile_reveal"; anchors.fill: parent; columns: 5
            Repeater { model: 20
                Rectangle { required property int index; width: root.width / 5; height: root.height / 4; color: index % 2 ? "#7b35bd" : "#dc3e87"; opacity: 0.76
                    SequentialAnimation on scale { running: parent.parent.visible; loops: Animation.Infinite
                        PauseAnimation { duration: index * 32 }
                        NumberAnimation { from: 1; to: 0.02; duration: 420; easing.type: Easing.InCubic }
                        PauseAnimation { duration: 900 }
                        NumberAnimation { to: 1; duration: 420; easing.type: Easing.OutBack }
                    }
                }
            }
        }

        Repeater {
            model: root.transitionStyle === "waveform_sweep" ? 35 : 0
            Rectangle { required property int index; width: Math.max(3, root.width * 0.006); radius: width / 2
                height: root.height * (0.04 + Math.abs(Math.sin(index * 0.72)) * 0.20); y: root.height * 0.5 - height / 2
                x: -root.width * 0.15 + index * root.width * 0.034; color: index % 2 ? "#42e7ff" : "#ff4f9a"; opacity: 0.72
                SequentialAnimation on x { running: root.active; loops: Animation.Infinite
                    NumberAnimation { from: -root.width * 0.15 + index * root.width * 0.034; to: root.width * 0.15 + index * root.width * 0.034; duration: 950; easing.type: Easing.InOutSine }
                    NumberAnimation { to: -root.width * 0.15 + index * root.width * 0.034; duration: 950; easing.type: Easing.InOutSine }
                }
            }
        }

        Rectangle {
            id: jukeboxArch; visible: root.transitionStyle === "jukebox_flip"; anchors.horizontalCenter: parent.horizontalCenter; y: root.height * 0.08
            width: root.width * 0.50; height: root.height * 0.80; radius: width / 2; color: "#181029"; border.width: 12; border.color: "#f6c945"; opacity: 0.58
            SequentialAnimation on scale { running: jukeboxArch.visible; loops: Animation.Infinite
                NumberAnimation { from: 0.94; to: 1.04; duration: 800; easing.type: Easing.InOutSine }
                NumberAnimation { to: 0.94; duration: 800; easing.type: Easing.InOutSine }
            }
        }

        Repeater {
            model: root.transitionStyle === "laser_fan" ? 9 : 0
            Rectangle { required property int index; width: 3; height: root.height * 1.1; x: root.width / 2; y: root.height * 0.32
                transformOrigin: Item.Bottom; rotation: -64 + index * 16; color: index % 2 ? "#58f2ff" : "#c26cff"; opacity: 0.54
                SequentialAnimation on rotation { running: root.active; loops: Animation.Infinite
                    NumberAnimation { from: -64 + index * 16; to: -54 + index * 16; duration: 900 + index * 45; easing.type: Easing.InOutSine }
                    NumberAnimation { to: -64 + index * 16; duration: 900 + index * 45; easing.type: Easing.InOutSine }
                }
            }
        }

        Item { anchors.fill: parent; visible: root.transitionStyle === "film_countdown"
            Rectangle { anchors.centerIn: parent; width: Math.min(root.width, root.height) * 0.72; height: width; radius: width / 2; color: "transparent"; border.width: 3; border.color: "#a8ead8b8" }
            Rectangle { id: filmSweep; width: 2; height: parent.height; color: "#70ead8b8"; opacity: 0.6
                SequentialAnimation on x { running: root.active && root.transitionStyle === "film_countdown"; loops: Animation.Infinite
                    NumberAnimation { from: root.width * 0.18; to: root.width * 0.80; duration: 180 }
                    PauseAnimation { duration: 520 }
                }
            }
        }

        Rectangle {
            id: polaroid; visible: root.transitionStyle === "polaroid_drop"; anchors.centerIn: parent
            width: root.width * 0.46; height: root.height * 0.58; color: "#f2eadf"; border.width: 10; border.color: "#f2eadf"; opacity: 0.42
            SequentialAnimation on rotation { running: polaroid.visible; loops: Animation.Infinite
                NumberAnimation { from: -14; to: 4; duration: 650; easing.type: Easing.OutBack }
                PauseAnimation { duration: 950 }
                NumberAnimation { to: 14; duration: 480; easing.type: Easing.InCubic }
            }
        }
    }

    Rectangle {
        id: flash
        anchors.fill: parent
        z: 20
        opacity: 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "#00f6c945" }
            GradientStop { position: 0.42; color: "#d8ffffff" }
            GradientStop { position: 0.62; color: "#c8b9a8ff" }
            GradientStop { position: 1.0; color: "#00ff65c8" }
        }
    }

    Item {
        id: shockwave
        anchors.centerIn: parent
        width: 120
        height: 120
        z: 18
        opacity: 0
        Repeater {
            model: 4
            Rectangle {
                required property int index
                anchors.centerIn: parent
                width: 112
                height: 112
                radius: 56
                color: "transparent"
                border.width: index === 1 ? 4 : 2
                border.color: index % 2 ? "#f6c945" : "#b9a8ff"
                scale: 1.0 + index * 0.13
                opacity: 0.92 - index * 0.14
            }
        }
    }

    Item {
        id: confettiBurst
        anchors.fill: parent
        z: 22
        visible: root.effectsEnabled && root.transitionStyle === "confetti_drop"
        Repeater {
            model: 42
            Item {
                id: particle
                required property int index
                readonly property real angle: (index / 42.0) * Math.PI * 2 + (index % 4) * 0.07
                readonly property real originX: root.width / 2
                readonly property real originY: root.height * 0.48
                readonly property real distanceX: root.width * (0.34 + (index % 5) * 0.045)
                readonly property real distanceY: root.height * (0.38 + (index % 6) * 0.055)
                x: originX
                y: originY
                width: 7 + (index % 4) * 3
                height: index % 3 === 0 ? width * 2.8 : width
                opacity: 0
                Rectangle {
                    anchors.fill: parent
                    radius: index % 3 === 0 ? 2 : width / 2
                    color: index % 5 === 0 ? "#ffffff" : (index % 5 === 1 ? "#f6c945" : (index % 5 === 2 ? "#a895ff" : (index % 5 === 3 ? "#ff65c8" : "#65e6ff")))
                }
                Connections {
                    target: root
                    function onBurstSerialChanged() { particleFlight.restart() }
                }
                ParallelAnimation {
                    id: particleFlight
                    XAnimator { target: particle; from: particle.originX; to: particle.originX + Math.cos(particle.angle) * particle.distanceX; duration: 760 + particle.index * 9; easing.type: Easing.OutCubic }
                    YAnimator { target: particle; from: particle.originY; to: particle.originY + Math.sin(particle.angle) * particle.distanceY + 70; duration: 820 + particle.index * 8; easing.type: Easing.OutCubic }
                    RotationAnimator { target: particle; from: 0; to: 320 + particle.index * 29; duration: 980; easing.type: Easing.OutQuad }
                    ScaleAnimator { target: particle; from: 0.25; to: 1.45; duration: 460; easing.type: Easing.OutBack }
                    SequentialAnimation {
                        OpacityAnimator { target: particle; from: 0.0; to: 1.0; duration: 70 }
                        PauseAnimation { duration: 330 + (particle.index % 6) * 28 }
                        OpacityAnimator { target: particle; from: 1.0; to: 0.0; duration: 360; easing.type: Easing.InQuad }
                    }
                }
            }
        }
    }

    Rectangle {
        id: nextPanel
        z: 10
        width: Math.min(root.width * 0.82, 1120)
        height: Math.min(root.height * 0.56, 500)
        x: (root.width - width) / 2
        y: (root.height - height) / 2
        radius: Math.max(22, height * 0.07)
        color: "#f018132c"
        border.width: 2
        border.color: "#a895ff"
        opacity: 0
        transformOrigin: Item.Center

        Rectangle {
            anchors.fill: parent
            anchors.margins: 7
            radius: parent.radius - 5
            color: "transparent"
            border.width: 1
            border.color: "#55f6c945"
        }
        Rectangle {
            id: panelSheen
            width: 150
            height: parent.height * 1.8
            y: -parent.height * 0.4
            rotation: 18
            opacity: 0.13
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#00ffffff" }
                GradientStop { position: 0.5; color: "#e0ffffff" }
                GradientStop { position: 1.0; color: "#00ffffff" }
            }
            XAnimator on x { running: root.active; from: -220; to: nextPanel.width + 220; duration: 2100; loops: Animation.Infinite; easing.type: Easing.InOutSine }
        }
        Column {
            anchors.centerIn: parent
            width: parent.width - 80
            spacing: Math.max(8, parent.height * 0.018)
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "NEXT UP  •  GET READY"
                color: "#f6c945"
                font.pixelSize: Math.max(16, nextPanel.height * 0.052)
                font.bold: true
                font.letterSpacing: 2.2
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.singerText
                color: "#ffffff"
                font.pixelSize: Math.max(48, nextPanel.height * 0.19)
                font.bold: true
                fontSizeMode: Text.Fit
                minimumPixelSize: Math.max(8, Math.min(30, root.height * 0.07))
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.songText
                color: "#e9e5f5"
                font.pixelSize: Math.max(24, nextPanel.height * 0.088)
                font.bold: true
                fontSizeMode: Text.Fit
                minimumPixelSize: Math.max(8, Math.min(18, root.height * 0.07))
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.artistText
                color: "#bfb7d8"
                font.pixelSize: Math.max(17, nextPanel.height * 0.058)
                fontSizeMode: Text.Fit
                minimumPixelSize: 8
                visible: text !== ""
            }
            Rectangle {
                id: countdownPill
                anchors.horizontalCenter: parent.horizontalCenter
                width: countdownText.implicitWidth + 34
                height: Math.max(46, nextPanel.height * 0.13)
                radius: height / 2
                color: "#4b3510"
                border.width: 2
                border.color: "#f6c945"
                Text {
                    id: countdownText
                    anchors.centerIn: parent
                    text: root.countdownValue > 0 ? root.countdownValue : "GO!"
                    color: "#ffffff"
                    font.pixelSize: Math.max(24, parent.height * 0.58)
                    font.bold: true
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: root.onDeckText !== "" ? "ON DECK  •  " + root.onDeckText : ""
                color: "#a99fc4"
                font.pixelSize: Math.max(14, nextPanel.height * 0.045)
                font.bold: true
                fontSizeMode: Text.Fit
                minimumPixelSize: 8
                visible: text !== ""
            }
        }
    }

    Item {
        id: startCountdownStage
        anchors.fill: parent
        z: 24
        opacity: 0
        visible: root.startCountdownActive

        Rectangle {
            anchors.fill: parent
            // A readable veil, not an opaque replacement for the selected
            // transition beneath it. The old #f2 alpha hid every shuffled
            // style and left only the common 3-2-1 sequence visible.
            color: "#52070612"
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: Math.max(42, root.height * 0.10)
            width: root.width * 0.94
            horizontalAlignment: Text.AlignHCenter
            text: "GET READY  •  " + root.singerText.toUpperCase()
            color: "#f6c945"
            font.pixelSize: Math.max(22, root.height * 0.046)
            font.bold: true
            font.letterSpacing: 3.2
            fontSizeMode: Text.Fit
            minimumPixelSize: 8
        }

        Rectangle {
            id: countdownRing
            anchors.centerIn: parent
            width: Math.min(root.width, root.height) * 0.48
            height: width
            radius: width / 2
            color: "#101b1433"
            border.width: Math.max(4, width * 0.018)
            border.color: root.startCountdownValue === 1 ? "#ff65c8" : (root.startCountdownValue === 2 ? "#65e6ff" : "#a895ff")
            opacity: 0
        }

        Text {
            id: countdownNumber
            anchors.centerIn: parent
            text: root.startCountdownValue > 0 ? root.startCountdownValue : ""
            color: "#ffffff"
            font.pixelSize: Math.min(root.width, root.height) * 0.42
            font.bold: true
            fontSizeMode: Text.Fit
            style: Text.Outline
            styleColor: root.startCountdownValue === 1 ? "#ff257e" : (root.startCountdownValue === 2 ? "#128cb8" : "#6245bd")
            transformOrigin: Item.Center
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.max(38, root.height * 0.09)
            width: root.width * 0.82
            horizontalAlignment: Text.AlignHCenter
            text: root.songText + (root.artistText !== "" ? "  •  " + root.artistText : "")
            color: "#d8d1ed"
            font.pixelSize: Math.max(19, root.height * 0.042)
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: Math.max(8, Math.min(16, root.height * 0.07))
        }
    }

    Column {
        id: outroPanel
        z: 25
        anchors.centerIn: parent
        width: root.width * 0.88
        spacing: Math.max(8, root.height * 0.016)
        opacity: 0
        transformOrigin: Item.Center

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "APPLAUSE!"
            color: "#f6c945"
            font.pixelSize: Math.max(24, root.height * 0.055)
            font.bold: true
            font.letterSpacing: 4.0
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.singerText
            color: "#ffffff"
            font.pixelSize: Math.max(76, root.height * 0.18)
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: Math.max(8, Math.min(42, root.height * 0.07))
            style: Text.Outline
            styleColor: "#713fd0"
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.songText + (root.artistText !== "" ? "  •  " + root.artistText : "")
            color: "#ddd6f4"
            font.pixelSize: Math.max(20, root.height * 0.045)
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: Math.max(8, Math.min(16, root.height * 0.07))
            visible: text !== ""
        }
    }

    Column {
        id: nowPanel
        z: 21
        anchors.centerIn: parent
        width: root.width * 0.86
        spacing: 8
        opacity: 0
        transformOrigin: Item.Center
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "NOW SINGING  •  LIVE"
            color: "#f6c945"
            font.pixelSize: Math.max(20, root.height * 0.045)
            font.bold: true
            font.letterSpacing: 2.4
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.singerText
            color: "#ffffff"
            font.pixelSize: Math.max(62, root.height * 0.13)
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: Math.max(8, Math.min(36, root.height * 0.07))
        }
        Text {
            width: parent.width
            horizontalAlignment: Text.AlignHCenter
            text: root.songText + (root.artistText !== "" ? "  •  " + root.artistText : "")
            color: "#ddd6f4"
            font.pixelSize: Math.max(22, root.height * 0.052)
            font.bold: true
            fontSizeMode: Text.Fit
            minimumPixelSize: Math.max(8, Math.min(16, root.height * 0.07))
        }
    }

    SequentialAnimation {
        id: startCountdownEntrance
        ParallelAnimation {
            OpacityAnimator { target: backdrop; from: 0.0; to: 0.96; duration: 180; easing.type: Easing.OutCubic }
            OpacityAnimator { target: startCountdownStage; from: 0.0; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
        }
    }

    SequentialAnimation {
        id: countdownNumberHit
        ParallelAnimation {
            ScaleAnimator { target: countdownNumber; from: 0.25; to: 1.16; duration: 260; easing.type: Easing.OutBack }
            OpacityAnimator { target: countdownNumber; from: 0.0; to: 1.0; duration: 90 }
            ScaleAnimator { target: countdownRing; from: 0.42; to: 1.08; duration: 430; easing.type: Easing.OutCubic }
            OpacityAnimator { target: countdownRing; from: 0.0; to: 0.82; duration: 110 }
        }
        ParallelAnimation {
            ScaleAnimator { target: countdownNumber; from: 1.16; to: 1.0; duration: 170; easing.type: Easing.OutBounce }
            OpacityAnimator { target: countdownRing; from: 0.82; to: 0.16; duration: 320; easing.type: Easing.InQuad }
        }
    }

    SequentialAnimation {
        id: nextUpEntrance
        ParallelAnimation {
            OpacityAnimator { target: backdrop; from: 0.0; to: 0.90; duration: 360; easing.type: Easing.OutCubic }
            OpacityAnimator { target: nextPanel; from: 0.0; to: 1.0; duration: 320; easing.type: Easing.OutCubic }
            ScaleAnimator { target: nextPanel; from: 0.66; to: 1.05; duration: 470; easing.type: Easing.OutBack }
        }
        ScaleAnimator { target: nextPanel; from: 1.05; to: 1.0; duration: 170; easing.type: Easing.OutBounce }
    }

    SequentialAnimation {
        id: countdownHit
        ParallelAnimation {
            ScaleAnimator { target: countdownPill; from: 1.0; to: 1.26; duration: 105; easing.type: Easing.OutQuad }
            RotationAnimator { target: countdownPill; from: -3; to: 3; duration: 105; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            ScaleAnimator { target: countdownPill; from: 1.26; to: 1.0; duration: 220; easing.type: Easing.OutBack }
            RotationAnimator { target: countdownPill; from: 3; to: 0; duration: 220; easing.type: Easing.OutBack }
        }
    }

    SequentialAnimation {
        id: singerStartSequence
        ParallelAnimation {
            OpacityAnimator { target: backdrop; from: 0.96; to: 0.82; duration: 130 }
            OpacityAnimator { target: flash; from: 0.0; to: root.burstStyle ? 0.95 : 0.0; duration: 75 }
            OpacityAnimator { target: shockwave; from: 0.0; to: root.burstStyle ? 1.0 : 0.0; duration: 70 }
            ScaleAnimator { target: shockwave; from: 0.15; to: 4.8; duration: 680; easing.type: Easing.OutCubic }
        }
        ParallelAnimation {
            OpacityAnimator { target: flash; from: root.burstStyle ? 0.95 : 0.0; to: 0.0; duration: 300; easing.type: Easing.OutCubic }
            OpacityAnimator { target: shockwave; from: root.burstStyle ? 1.0 : 0.0; to: 0.0; duration: 360; easing.type: Easing.OutQuad }
            OpacityAnimator { target: nowPanel; from: 0.0; to: 1.0; duration: 280; easing.type: Easing.OutCubic }
            ScaleAnimator { target: nowPanel; from: 0.40; to: 1.12; duration: 480; easing.type: Easing.OutBack }
            RotationAnimator { target: nowPanel; from: -5; to: 0; duration: 480; easing.type: Easing.OutBack }
        }
        ScaleAnimator { target: nowPanel; from: 1.12; to: 1.0; duration: 180; easing.type: Easing.OutBounce }
        PauseAnimation { duration: 1200 }
        ParallelAnimation {
            OpacityAnimator { target: nowPanel; from: 1.0; to: 0.0; duration: 420; easing.type: Easing.InCubic }
            ScaleAnimator { target: nowPanel; from: 1.0; to: 1.16; duration: 420; easing.type: Easing.InCubic }
            OpacityAnimator { target: backdrop; from: 0.82; to: 0.0; duration: 480; easing.type: Easing.InCubic }
        }
        ScriptAction { script: root.active = false }
    }

    SequentialAnimation {
        id: songOutroSequence
        ParallelAnimation {
            OpacityAnimator { target: backdrop; from: 0.0; to: 0.92; duration: 150; easing.type: Easing.OutCubic }
            OpacityAnimator { target: flash; from: 0.0; to: root.burstStyle ? 1.0 : 0.0; duration: 70 }
            OpacityAnimator { target: shockwave; from: 0.0; to: root.burstStyle ? 1.0 : 0.0; duration: 60 }
            ScaleAnimator { target: shockwave; from: 0.10; to: 5.6; duration: 620; easing.type: Easing.OutCubic }
            OpacityAnimator { target: outroPanel; from: 0.0; to: 1.0; duration: 210; easing.type: Easing.OutCubic }
            ScaleAnimator { target: outroPanel; from: 0.36; to: 1.14; duration: 480; easing.type: Easing.OutBack }
        }
        ParallelAnimation {
            OpacityAnimator { target: flash; from: root.burstStyle ? 1.0 : 0.0; to: 0.0; duration: 240; easing.type: Easing.OutCubic }
            OpacityAnimator { target: shockwave; from: root.burstStyle ? 1.0 : 0.0; to: 0.0; duration: 320; easing.type: Easing.OutQuad }
            ScaleAnimator { target: outroPanel; from: 1.14; to: 1.0; duration: 170; easing.type: Easing.OutBounce }
        }
        ScriptAction { script: root.burstSerial += 1 }
        ParallelAnimation {
            OpacityAnimator { target: flash; from: 0.0; to: root.burstStyle ? 0.52 : 0.0; duration: 65 }
            ScaleAnimator { target: outroPanel; from: 1.0; to: 1.07; duration: 120; easing.type: Easing.OutQuad }
        }
        ParallelAnimation {
            OpacityAnimator { target: flash; from: root.burstStyle ? 0.52 : 0.0; to: 0.0; duration: 180; easing.type: Easing.OutCubic }
            ScaleAnimator { target: outroPanel; from: 1.07; to: 1.0; duration: 170; easing.type: Easing.OutBack }
        }
        PauseAnimation { duration: 520 }
        ParallelAnimation {
            OpacityAnimator { target: outroPanel; from: 1.0; to: 0.0; duration: 280; easing.type: Easing.InCubic }
            ScaleAnimator { target: outroPanel; from: 1.0; to: 1.20; duration: 280; easing.type: Easing.InCubic }
            OpacityAnimator { target: backdrop; from: 0.92; to: 0.0; duration: 340; easing.type: Easing.InCubic }
        }
        ScriptAction { script: root.active = false }
    }

    SequentialAnimation {
        id: overlayExit
        ParallelAnimation {
            OpacityAnimator { target: nextPanel; from: nextPanel.opacity; to: 0.0; duration: 200; easing.type: Easing.InCubic }
            ScaleAnimator { target: nextPanel; from: nextPanel.scale; to: 1.06; duration: 200; easing.type: Easing.InCubic }
            OpacityAnimator { target: nowPanel; from: nowPanel.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: outroPanel; from: outroPanel.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: startCountdownStage; from: startCountdownStage.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: countdownNumber; from: countdownNumber.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: countdownRing; from: countdownRing.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: flash; from: flash.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: shockwave; from: shockwave.opacity; to: 0.0; duration: 180; easing.type: Easing.InCubic }
            OpacityAnimator { target: backdrop; from: backdrop.opacity; to: 0.0; duration: 240; easing.type: Easing.InCubic }
        }
        ScriptAction { script: root.active = false }
    }
}

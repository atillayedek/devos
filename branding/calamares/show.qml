import QtQuick 2.0;
import calamares.slideshow 1.0;

Presentation {
    id: presentation
    function nextSlide() { presentation.goToNextSlide(); }
    Timer { id: advanceTimer; interval: 9000; running: presentation.activatedInCalamares; repeat: true; onTriggered: nextSlide() }

    Repeater {
        model: [
            ["DevOS — a full developer workstation", "IDEs, SDKs, compilers, containers, Android and AI coding tools are installed for you. No hours of setup after the first login."],
            ["Code your way", "Write code by hand in VS Code, VSCodium, JetBrains IDEs, Neovim or Emacs — or describe an app in Vibe Coding and let an AI agent build it in ~/Developer."],
            ["Android & Flutter ready", "Android Studio, the Android SDK, NDK, emulator and Flutter are configured with ANDROID_HOME, JAVA_HOME and USB rules in place."],
            ["Every language", "Node.js, Bun, Deno, Python + uv, Java + Kotlin, C/C++, .NET, Rust, Go and PHP — plus PostgreSQL, MariaDB, Redis, Docker and Podman."],
            ["Accounts are optional", "Connect GitHub, OpenAI, Anthropic, Google or cloud accounts in Accounts & AI only if you want to. Secrets live in the system keyring."],
            ["Windows apps", "Run Windows programs with Wine or Bottles; Visual Studio and other Windows-only tools run in the built-in Windows Development VM."],
            ["Honest status", "Developer Center shows READY only when every required tool really passes its health check."]
        ]
        Slide {
            Rectangle { anchors.fill: parent; color: "#0b1020" }
            Text {
                id: title
                anchors.horizontalCenter: parent.horizontalCenter; y: parent.height * 0.3
                text: modelData[0]; color: "#ffffff"; font.pixelSize: 30; font.bold: true
                width: parent.width * 0.85; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter; anchors.top: title.bottom; anchors.topMargin: 24
                text: modelData[1]; color: "#c7d2fe"; font.pixelSize: 18
                width: parent.width * 0.75; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            }
        }
    }
    function onActivate() { presentation.currentSlide = 0; }
    function onLeave() { }
}

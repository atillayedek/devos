# DevOS developer environment — SDK locations and PATH for every login shell.
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT=/opt/android-sdk
export JAVA_HOME=/usr/lib/jvm/default-java
export RUSTUP_HOME=/opt/rust/rustup
export DOTNET_ROOT=/opt/dotnet
export DOTNET_CLI_TELEMETRY_OPTOUT=1
export FLUTTER_ROOT=/opt/flutter
if [ -r /etc/devos/chrome-executable ]; then
  CHROME_EXECUTABLE=$(cat /etc/devos/chrome-executable); export CHROME_EXECUTABLE
fi
devos_path_add() { case ":$PATH:" in *":$1:"*) ;; *) [ -d "$1" ] && PATH="$PATH:$1" ;; esac; }
for d in /opt/devos/npm/bin /opt/flutter/bin /opt/android-sdk/cmdline-tools/latest/bin \
         /opt/android-sdk/platform-tools /opt/android-sdk/emulator /usr/local/go/bin \
         /opt/rust/cargo/bin /opt/dotnet "$HOME/.local/bin" "$HOME/.cargo/bin" "$HOME/go/bin" \
         "$HOME/.dotnet/tools" "$HOME/.pub-cache/bin"; do
  devos_path_add "$d"
done
unset -f devos_path_add
export PATH

#!/bin/sh
# Buduje aplikacje i wystawia je jako aktualizacje dla telefonów grupy.
# Kanały (od 0.18): user = SkywatchPL Czujnik, dev = Czujnik z diagnostyką, alarm = SkywatchPL Alarm.
# Użycie: publish_apk.sh [user|dev|alarm ...]  (domyślnie wszystkie)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
. /home/openclaw/opt/android-env.sh
cd "$HERE/../app/android"
CHANNELS=${*:-"user dev alarm"}
variant() { case "$1" in user) echo userSensor ;; dev) echo devSensor ;; alarm) echo userAlarm ;; *) echo "nieznany kanał $1" >&2; exit 1 ;; esac; }
TASKS=$(for c in $CHANNELS; do printf 'assemble%sDebug ' "$(variant "$c" | sed 's/./\U&/')"; done)
gradle $TASKS --console=plain -q -Pkotlin.compiler.execution.strategy=in-process
for c in $CHANNELS; do
  V=$(variant "$c")
  APK=app/build/outputs/apk/$V/debug/app-$(echo "$V" | sed 's/\([A-Z]\)/-\L\1/g')-debug.apk
  mkdir -p "$HERE/dist/$c"
  cp "$APK" "$HERE/dist/$c/skywatch.apk.tmp"
  BADGE=$("$ANDROID_HOME"/build-tools/36.1.0/aapt2 dump badging "$APK" | head -1)
  CODE=$(echo "$BADGE" | sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p")
  NAME=$(echo "$BADGE" | sed -n "s/.*versionName='\([^']*\)'.*/\1/p")
  mv "$HERE/dist/$c/skywatch.apk.tmp" "$HERE/dist/$c/skywatch.apk"
  printf '{"versionCode": %s, "versionName": "%s"}\n' "$CODE" "$NAME" > "$HERE/dist/$c/version.json"
  echo "wystawiono $c ($V): $NAME ($CODE)"
done

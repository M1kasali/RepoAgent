#!/bin/sh
set -eu

export HOME="${PWD}/.repoagent-home"
export GRADLE_USER_HOME="${HOME}/.gradle"
export GOCACHE="${HOME}/.cache/go-build"
export GOTMPDIR="${HOME}/tmp/go"
export NPM_CONFIG_CACHE="${HOME}/.npm"
export TMPDIR=/tmp
mkdir -p \
    "${GRADLE_USER_HOME}" \
    "${GOCACHE}" \
    "${GOTMPDIR}" \
    "${NPM_CONFIG_CACHE}"
case " $* " in
    *" gradle "*)
        cp -a /opt/gradle-cache/. "${GRADLE_USER_HOME}/"
        chmod -R u+w "${GRADLE_USER_HOME}"
        ;;
esac

exec "$@"

#!/bin/sh
set -eu

mkdir -p build
cd build
cmake -DEXERCISM_RUN_ALL_TESTS=1 -G "Unix Makefiles" ..
cmake --build .

#!/bin/sh
set -e

git push origin -f knix filter-state 'refs/tags/v*-dts' 'refs/tags/v*-dts-raw'


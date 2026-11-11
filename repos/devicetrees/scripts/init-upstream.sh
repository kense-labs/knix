#!/bin/sh
set -e

git remote add state https://github.com/ijc/devicetree-conversion-state-v2
git remote add knix https://github.com/knix/linux
git fetch origin filter-state knix-branch-base
git branch filter-state refs/remotes/origin/filter-state

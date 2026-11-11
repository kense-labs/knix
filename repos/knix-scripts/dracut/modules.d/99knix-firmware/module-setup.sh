#!/bin/sh
# SPDX-License-Identifier: MIT

# called by dracut
check() {
    if [ -n "$hostonly" ] && [ ! -e /proc/device-tree/chosen/knix,efi-system-partition ]; then
       return 0
    elif [ -z "$hostonly" ]; then
        return 0
    else
       return 255
    fi
}

# called by dracut
depends() {
    echo fs-lib
    return 0
}

# called by dracut
installkernel() {
    instmods apple-mailbox nvme-apple vfat
}

# called by dracut
install() {
    inst_dir "/lib/firmware"
    ln_r "/vendorfw" "/lib/firmware/vendor"
    knixscriptsdir="/usr/share/knix-scripts"
    inst_dir $knixscriptsdir
    $DRACUT_CP -R -L -t "${initdir}/${knixscriptsdir}" "${dracutsysrootdir}${knixscriptsdir}"/*
    inst_multiple cpio cut dirname grep mkdir modprobe mount seq sleep umount
    inst_hook pre-udev 10 "${moddir}/load-knix-firmware.sh"
    inst_hook cleanup 99 "${moddir}/install-knix-firmware.sh"
}

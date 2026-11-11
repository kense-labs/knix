---
title: Undoing Early Speaker Hacks
---

### Introduction
You are probably here because you tried to enable your speakers early, and a change in config
between when you did this and the public release of speaker support broke something. You were
warned.

Below are some fixes for common early adopter hacks. Please try _all_ of them before filing a bug.
Your bug will be ignored if we find that you have failed to rectify any of the issues
below.

### The Pro Audio profile is/was enabled for the internal speakers / headphones
This one can happen in one of three ways:
* You have changed the profile while headphones were plugged in
* You have been using a very, very, _very_ old version of `knix-audio`
* You have circumvented Wireplumber node permissions to experiment with device profiles

In the first case change the profile back to `Default` (HiFi). In KDE's Audio settings
change the profile back to `Default`. If no headphones are plugged in press
`Show Inactive Devices`. The same should be possible with applications like `pavucontrol`.
In doubt delete WirePlumber's sstate directory (`rm -rf ~/.local/state/wireplumber/`)
and reboot.

The fix in the two other cases is the the same:
1. `rm -rf ~/.local/state/wireplumber/`
2. Reinstall `knix-audio`, Pipewire _and_ Wireplumber
3. Reboot your machine

### You have files in /etc/ from a prerelease version of knix-audio
Very old versions of `knix-audio` stored their configuration inside `/etc/pipewire/` and
`/etc/wireplumber/`. There should be nothing Knix related in _either_ of these directories
or any of their subdirectories. To fix this: 
```sh
rm -rf /etc/wireplumber/wireplumber.conf.d/*knix*
rm -rf /etc/wireplumber/main.lua.d/*knix*
rm -rf /etc/wireplumber/policy.lua.d/*knix*
rm -rf /etc/pipewire/pipewire.conf.d/*knix*
```
Once you have done this, reinstall `knix-audio`, Pipewire _and_ Wireplumber then reboot
your system.

### You have files in /usr/share/ from a prerelease version of knix-audio
Prerelease versions of `knix-audio` had files in `/usr/share/` that do not match the ones
that shipped with 1.0. These files can conflict with the release versions, causing issues.
You must manually remove all `knix-audio` files:
```sh
rm -rf /usr/share/knix-audio/
rm -rf /usr/share/wireplumber/wireplumber.conf.d/*knix*
rm -rf /usr/share/wireplumber/main.lua.d/*knix*
rm -rf /usr/share/wireplumber/policy.lua.d/*knix*
rm -rf /usr/share/pipewire/pipewire.conf.d/*knix*
```
Once you have done this, reinstall `knix-audio`, Pipewire _and_ Wireplumber then reboot
your system.

### You have tried to manually circumvent our kernel-level safety controls
Remove `snd_soc_macaudio.please_blow_up_my_speakers` from wherever you added it. This could be
the default kernel command line, `modprobe.d`, or somewhere else. Reboot when this is done.

### Required speaker codec settings are not being applied
This can happen if you are on an old kernel, or you have manually set `snd_soc_tas2764.apple_quirks`
to some nonstandard value. As above, remove any reference to this module parameter, update your kernel,
then reboot.

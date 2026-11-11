# Asahi Linux Plymouth Theme

This is a Plymouth theme for the Asahi Linux distribution for Apple Silicon Macs.

## Installing (works for most distros)
You should have Plymouth installed and hooked into your initrd.

1. Clone this repo somewhere.
2. Copy the `asahi` folder to `/usr/share/plymouth/themes/`.
3. Run `plymouth-set-default-theme -R asahi` as root.

## Testing
1. Run `plymouthd --no-daemon --debug` as root in tty2.
2. Run `plymouth show-splash` in tty3. The splash screen
will appear in tty1.
* Running `plymouth` with no arguments will give you a list of
modes and parameters you can test.

## What works
* The Asahi Linux logo shows up
* The progress bar fills up while booting
* Boot time messages appear below the progress bar

## Yet to be implemented
Password/text entry (required for users with LUKS-encrypted disks, etc.)
Currently, Plymouth will fall back to the tty for user input.

## Attributions
* The Asahi Linux logo was created by <a href="https://soundflora.tokyo/">soundflora*</a> and <a href="https://github.com/marcan/">Hector Martin</a>. It is available under the CC BY-SA 4.0 license.

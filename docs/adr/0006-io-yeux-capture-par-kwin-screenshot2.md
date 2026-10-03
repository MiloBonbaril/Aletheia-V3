# io_yeux captures the screen through KWin ScreenShot2, and this authorizes every Python 3.14 script

`io_yeux` captures the screen of the streamer once each second. The host is KDE Plasma 6 on
Wayland, with two screens. On Wayland, a client cannot read the pixels of other windows: the X11
methods (`mss`, `xdotool`, `import`) do not operate. The service needs three things: the active
screen or a named screen, chosen again at each capture; the pixels in memory, never in a file; and
a cost of some tens of milliseconds, because the capture runs at 1 Hz.

**We use the D-Bus interface `org.kde.KWin.ScreenShot2`.** It has `CaptureActiveScreen`,
`CaptureScreen(name)`, `CaptureActiveWindow` and `CaptureArea`. KWin writes the raw image into a
pipe that the caller gives, and returns the width, the height, the stride and the format. The
service reads the pipe into memory. There is no file and no consent dialog. The dependency is
`dbus-next`, which is pure Python and passes Unix file descriptors.

**The cost is a security compromise.** KWin restricts this interface. It accepts a call only from
an executable that a `.desktop` file declares with
`X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`. KWin identifies the caller by its
executable, not by its script: it reads `/proc/<pid>/exe` of the caller and compares it with the
first word of the `Exec` line of each application `.desktop` file. `NoDisplay=true` keeps the file
out of the application menu, and KWin still reads it. Verified on KWin 6.7.5: the authorization
operates as soon as the file is in place, with no `kbuildsycoca6` and no new session. For `io_yeux`, the executable is the interpreter of the venv, and
`venv/bin/python` resolves to `/usr/bin/python3.14`. Thus the `.desktop` file authorizes **every
Python 3.14 script of the session** to capture the screen without a dialog. We accept this: the
machine is a single-user workstation, and the scripts that run on it are the scripts of the
project. The file `services/io_yeux/io_yeux.desktop` is in the repository. You install it by hand
in `~/.local/share/applications/`. It is not installed automatically, thus the authorization stays
a visible act. To remove the authorization, delete that file.

**Considered options**:

- The XDG ScreenCast portal with a PipeWire stream — rejected. It is the portable method of
  Wayland, and it has no global authorization. But the user selects the source once in a dialog,
  thus "the active screen" cannot change between two captures. It also needs GStreamer and the
  `gi` bindings in the venv, and much more code.
- `spectacle -b -n -o /dev/shm/…` in a subprocess — rejected. It starts a Qt application at each
  capture, which costs some hundreds of milliseconds of CPU at 1 Hz, and the image goes through a
  file.
- The environment variable `KWIN_SCREENSHOT_NO_PERMISSION_CHECKS=1` on KWin — rejected. It removes
  the check for every process of the session, not only for Python.

**Consequences**: `io_yeux` operates on KDE Plasma only. Portability to another compositor is not
an objective, because the service observes one specific workstation. If the system Python changes
version, the `Exec` line of the `.desktop` file must change too, or the capture fails with an
authorization error. The README of `io_yeux` gives this procedure. The service is in no profile of
`services/terminal`: you start it by hand, and to stop the capture you stop the service.

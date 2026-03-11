#!/bin/sh
# Launch xorgwizard on a connected EXTERNAL monitor (HDMI/DP/VGA), regardless of resolution/layout.

sleep 2

# If xrandr isn't usable yet, just exit quietly.
command -v xrandr >/dev/null 2>&1 || exit 0

# Pick the first connected external output we can find.
# (We intentionally skip eDP/LVDS/DSI because those are internal panels.)
OUT="$(
  xrandr 2>/dev/null |
  awk '
    /^[A-Za-z0-9-]+ connected/ {
      name=$1
      if (name ~ /^(eDP|LVDS|DSI)/) next
      print name
      exit
    }
  '
)"

[ -n "$OUT" ] || exit 0

# Extract the +X+Y offset for that output from its xrandr line (e.g., 3840x2160+1366+0).
OFF="$(
  xrandr 2>/dev/null |
  awk -v out="$OUT" '
    $1==out && $2=="connected" {
      for (i=1;i<=NF;i++) if ($i ~ /\+[0-9]+\+[0-9]+$/) { print $i; exit }
    }
  '
)"

# Default placement if we couldn't parse offsets
X=50
Y=50

if [ -n "$OFF" ]; then
  # Parse "...+X+Y"
  X="$(echo "$OFF" | sed -n 's/.*+\([0-9]\+\)+\([0-9]\+\)$/\1/p')"
  Y="$(echo "$OFF" | sed -n 's/.*+\([0-9]\+\)+\([0-9]\+\)$/\2/p')"
  # Nudge inside the monitor so the window isn't on the border
  X=$((X + 50))
  Y=$((Y + 50))
fi

# Launch on the external screen by positioning the window in that screen's coordinate space.
# (If your xorgwizard ignores -geometry on your Puppy build, tell me and I’ll give the wmctrl fallback.)
xorgwizard -geometry 900x650+${X}+${Y} >/dev/null 2>&1 &
exit 0

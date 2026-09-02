"""
Audible fault alarm.

WHAT THIS IS FOR, AND WHAT IT IS NOT FOR
---------------------------------------
This sounds when the SYSTEM is broken: the feed has gone stale, or the canary
has failed and the evaluation path is dead. That is the operator page the
design already requires -- "loss of confidence -> no alert AND a page" -- and
until now that page was a log line on a headless box that nobody reads.

It does NOT sound on a hazard decision. An audible alert on advisory/warning
would be a dispatch channel, and Phase 1 does not warn anyone; that is what
--allow-dispatch gates and what the institutional owner has to authorise.
Wiring a buzzer to hazard detection would route around a non-negotiable with
a jumper wire. Do not do it.

So: a quiet Pi means the watcher is healthy. A buzzing Pi means go and look
at the watcher -- never that a flood is coming.

OUTPUTS, tried in order
-----------------------
  gpio    a piezo buzzer on a GPIO pin, driven through pinctrl. Best option:
          loud, no audio stack, works headless, a few pounds.

          IT MUST BE AN *ACTIVE* BUZZER MODULE. An active buzzer has its own
          oscillator and sounds when the pin goes high, which is all
          _buzz_gpio does. A PASSIVE buzzer needs a PWM square wave and will
          stay silent here -- it is the single most common way this ends up
          not working. Prefer a 3-pin module (VCC/GND/I-O, e.g. KY-012)
          over a bare element: the module has the driver transistor, so the
          GPIO pin is not asked to supply the current directly.
  led     the Pi's ACT LED, flashed in a distinctive pattern. Silent, but
          for a headless box sitting on a desk it is a real indicator and it
          needs no hardware at all. Requires write access to
          /sys/class/leds/ACT, which in practice means passwordless sudo.
  aplay   ALSA. Needs something that actually makes sound -- a USB audio
          device, or HDMI with a display attached. On a Pi 5 there is no
          3.5mm jack, and ALSA will happily accept audio for a disconnected
          HDMI port and play it to nothing.
  none    logs loudly and carries on.

Never raises. An alarm that crashes the watcher is worse than no alarm.
"""

import logging
import math
import os
import shutil
import struct
import subprocess
import tempfile
import time
import wave

log = logging.getLogger("hew.alarm")

# Do not re-sound the same fault more often than this. A fault that persists
# for hours should not produce a fault noise every 60 seconds forever -- the
# operator stops hearing it, which is how alarms get taped over.
REARM_SECONDS = 900

_last = {}


# -- output detection -------------------------------------------------------

def gpio_pin():
    """Buzzer pin from the environment, e.g. HEW_BUZZER_PIN=17."""
    v = os.environ.get("HEW_BUZZER_PIN")
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        log.warning("HEW_BUZZER_PIN=%r is not an integer, ignoring", v)
        return None


def _hdmi_connected():
    try:
        import glob
        for s in glob.glob("/sys/class/drm/*HDMI*/status"):
            with open(s) as f:
                if f.read().strip() == "connected":
                    return True
    except OSError:
        pass
    return False


LED_PATH = "/sys/class/leds/ACT"


def _led_writable():
    """Direct write, or a non-interactive sudo. Nothing else."""
    if not os.path.isdir(LED_PATH):
        return None
    if os.access(os.path.join(LED_PATH, "brightness"), os.W_OK):
        return "direct"
    if shutil.which("sudo"):
        r = subprocess.run(["sudo", "-n", "test", "-w",
                            os.path.join(LED_PATH, "brightness")],
                           capture_output=True)
        if r.returncode == 0:
            return "sudo"
    return None


def _led_write(name, value, how):
    path = os.path.join(LED_PATH, name)
    if how == "direct":
        with open(path, "w") as f:
            f.write(str(value))
    else:
        subprocess.run(["sudo", "-n", "sh", "-c", f"echo {value} > {path}"],
                       check=False, timeout=5, capture_output=True)


def _flash_led(how, reps=6, on=0.12, off=0.12):
    """
    Take the LED, blink it, give it back. The ACT LED normally shows disk
    activity (trigger mmc0); leaving it detached would quietly remove a
    diagnostic someone else may rely on.
    """
    prev = "mmc0"
    try:
        with open(os.path.join(LED_PATH, "trigger")) as f:
            cur = f.read()
        import re as _re
        m = _re.search(r"\[([a-z0-9-]+)\]", cur)
        if m:
            prev = m.group(1)
    except OSError:
        pass
    try:
        _led_write("trigger", "none", how)
        for _ in range(reps):
            _led_write("brightness", 1, how)
            time.sleep(on)
            _led_write("brightness", 0, how)
            time.sleep(off)
    finally:
        _led_write("trigger", prev, how)


def _usb_audio():
    """An ALSA card that is not the (possibly unplugged) HDMI output."""
    try:
        with open("/proc/asound/cards") as f:
            for line in f:
                if "[" in line and "vc4hdmi" not in line:
                    return True
    except OSError:
        pass
    return False


def available():
    """(method, why) -- what this machine can actually make a noise with."""
    if gpio_pin() is not None and shutil.which("pinctrl"):
        return "gpio", f"piezo buzzer on GPIO {gpio_pin()}"
    if shutil.which("aplay"):
        if _usb_audio():
            return "aplay", "USB audio device"
        if _hdmi_connected():
            return "aplay", "HDMI audio (display attached)"
    if _led_writable():
        return "led", ("ACT LED (silent). No audio device: no USB audio, "
                       "HDMI disconnected, Pi 5 has no 3.5mm jack.")
    return "none", "no buzzer, no audio output, no writable LED"


# -- making the noise -------------------------------------------------------

def _tone_wav(path, freq=880, seconds=0.18, rate=16000, reps=3, gap=0.12):
    """
    A short repeated tone. Deliberately not a pleasant sound, and deliberately
    distinct from a notification chime: this means a machine has stopped
    working, and it should be annoying enough to investigate.
    """
    frames = bytearray()
    for _ in range(reps):
        for i in range(int(rate * seconds)):
            # fade the edges so it clicks less and carries better
            env = min(1.0, i / (rate * 0.01),
                      (rate * seconds - i) / (rate * 0.01))
            v = int(18000 * env * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", v)
        frames += b"\x00\x00" * int(rate * gap)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def _buzz_gpio(pin, reps=3, on=0.18, off=0.12):
    for _ in range(reps):
        subprocess.run(["pinctrl", "set", str(pin), "op", "dh"],
                       check=False, timeout=5)
        time.sleep(on)
        subprocess.run(["pinctrl", "set", str(pin), "op", "dl"],
                       check=False, timeout=5)
        time.sleep(off)


def sound(reason, force=False):
    """
    Sound the fault alarm. Returns True if a noise was actually produced.

    Rate-limited per reason. Never raises: any failure here is logged and
    swallowed, because a broken alarm must not take the watcher with it.
    """
    now = time.time()
    if not force and now - _last.get(reason, 0) < REARM_SECONDS:
        return False
    _last[reason] = now

    method, why = available()
    log.error("FAULT ALARM: %s  [%s: %s]", reason, method, why)
    try:                                   # push to the operator's phone
        from . import hew_operator as operator
        if operator.configured():
            operator.fault(reason, f"alarm output: {method} ({why})")
    except Exception as e:
        log.error("operator push failed: %s", e)
    if method == "none":
        return False
    try:
        if method == "gpio":
            _buzz_gpio(gpio_pin())
        elif method == "led":
            _flash_led(_led_writable())
        else:
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                _tone_wav(path)
                subprocess.run(["aplay", "-q", path], check=False, timeout=20)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        return True
    except Exception as e:                      # never break the caller
        log.error("alarm failed (%s) — continuing", e)
        return False


def reset(reason=None):
    """Clear the rate limiter, so recovery then re-failure sounds again."""
    if reason is None:
        _last.clear()
    else:
        _last.pop(reason, None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    m, w = available()
    print(f"output: {m}  ({w})")
    print("sounding test alarm ...")
    print("produced sound:", sound("self-test", force=True))

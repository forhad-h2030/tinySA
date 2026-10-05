"""
Set the SMB100B to a single CW tone, with safety checks for the tinySA connected to it.
    python set-single-bin.py                       # uses the defaults below
    python set-single-bin.py --power 5             # a different level, up to --max-dbm
    python set-single-bin.py --freq 213.05 --power -20
"""

import argparse
import sys
from generator_state import open_generator

SMA_IP_ADDRESS = "192.168.1.10"          # or host:port for a tunnelled SCPI socket, e.g. localhost:5025

# TARGET BINS
target_freq_mhz = 213.0
target_power_dbm = -10.0

# Highest level this script will ever request. Keep it well under the tinySA's +10 dBm input limit.
MAX_SAFE_DBM = 6.0

def drain_errors(sma):
    """Return the generator's queued SCPI errors (empty list means none)."""
    errors = []
    for _ in range(10):
        reply = sma.query("SYSTem:ERRor?").strip()
        if reply.startswith("0"):
            break
        errors.append(reply)
    return errors


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", default=SMA_IP_ADDRESS, help="generator address, or host:port for the SCPI socket")
    p.add_argument("--freq", type=float, default=target_freq_mhz, help="frequency in MHz")
    p.add_argument("--power", type=float, default=target_power_dbm, help="level in dBm")
    p.add_argument("--max-dbm", type=float, default=MAX_SAFE_DBM, help="highest level allowed (default %(default)g)")
    p.add_argument("--keep-limit", action="store_true", help="leave the generator's Level Limit at --max-dbm on exit")
    args = p.parse_args()

    # Refuse before touching the generator.
    if args.power > args.max_dbm:
        sys.exit(f"Refusing: {args.power:g} dBm is above the safe maximum of {args.max_dbm:g} dBm. "
                 "Add an attenuator and raise --max-dbm only if you are sure.")

    sma = open_generator(args.ip, 5000)
    original_limit = None
    try:
        print(f"Connected to RF Gen: {sma.query('*IDN?').strip()}")

        try:
            original_limit = float(sma.query("SOURce1:POWer:LIMit:AMPLitude?"))
        except Exception:
            print("Warning: could not read the generator's Level Limit; relying on the script's own check.")

        sma.write("*RST")                       # also switches RF off and does not change the limit
        sma.query("*OPC?")

        if original_limit is not None:
            limit = min(original_limit, args.max_dbm)   # never raise an existing, lower limit
            sma.write(f"SOURce1:POWer:LIMit:AMPLitude {limit}")
            if args.power > limit + 0.005:
                sys.exit(f"Refusing: the generator's Level Limit is {limit:g} dBm, below the requested "
                         f"{args.power:g} dBm. Its Level display would still show {args.power:g} dBm "
                         "while the real output stayed capped.")

        freq_hz = args.freq * 1e6
        print(f"\nSetting single frequency bin to {args.freq} MHz at {args.power} dBm...")
        sma.write(f"SOURce:FREQuency:CW {freq_hz}")
        sma.write(f"SOURce:POWer:LEVel {args.power}")
        sma.query("*OPC?")

        errors = drain_errors(sma)
        if errors:
            sys.exit(f"The generator reported errors, so RF stays off: {errors}")

        # --- VERIFY THE ACTUAL STATUS before switching RF on ---
        current_freq = float(sma.query("SOURce:FREQuency:CW?"))
        current_pow = float(sma.query("SOURce:POWer:LEVel?"))
        current_limit = float(sma.query("SOURce1:POWer:LIMit:AMPLitude?")) if original_limit is not None else None
        print("\nVerification from Hardware:")
        print(f"-> Active Frequency: {current_freq / 1e6:.6f} MHz")
        print(f"-> Active Power Level: {current_pow:.2f} dBm")
        if current_limit is not None:
            print(f"-> Level Limit: {current_limit:.2f} dBm")
        if current_pow > args.max_dbm + 0.005 or (current_limit is not None and current_pow > current_limit + 0.005):
            sys.exit("The generator's level is above the safe maximum or its limit, so RF stays off.")

        sma.write("OUTPut:STATe ON")
        if int(float(sma.query("OUTPut:STATe?"))) != 1:
            sys.exit("The generator did not switch RF on.")

        input(f"\nRF Output is LIVE at {args.freq} MHz @ {args.power} dBm. "
              "Press Enter to turn off output and close...")
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        try:
            sma.write("OUTPut:STATe OFF")
            print("RF output switched OFF.")
        except Exception as e:
            print(f"WARNING: could not confirm RF OFF ({e}). Check the generator's front panel.")
        if original_limit is not None and not args.keep_limit:
            try:
                sma.write(f"SOURce1:POWer:LIMit:AMPLitude {original_limit}")
            except Exception as e:
                print(f"Warning: could not restore the Level Limit to {original_limit:g} dBm ({e}).")
        sma.close()
        print("Generator disconnected.")


if __name__ == "__main__":
    main()

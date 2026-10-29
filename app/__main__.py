"""Console entry point."""

from __future__ import annotations

import argparse
import json
import sys

from .engineer import AlertConfig
from .history import DEFAULT_DB, RaceHistory
from .logging_setup import LOGGER, create_log_handler
from .strategy import StrategyAssumptions
from .telemetry.receiver import HOST, PORT, replay_capture, run_receiver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only F1 22 telemetry companion")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--record", metavar="NAME.f1cap", help="opt in to a bounded raw packet capture")
    mode.add_argument("--replay", metavar="NAME.f1cap", help="replay a capture from the project recordings folder")
    mode.add_argument("--debrief", metavar="SESSION_UID", help="show a stored session debrief")
    mode.add_argument("--export-history", nargs=3, metavar=("SESSION_UID", "csv|json", "FILE"),
                      help="export a stored session")
    parser.add_argument("--alert-cooldown", type=float, default=60.0, metavar="SECONDS",
                        help="minimum time between repeats of the same alert (default: 60)")
    parser.add_argument("--pit-loss", type=float, metavar="SECONDS", help="measured normal pit-lane loss")
    parser.add_argument("--fresh-tyre-gain", type=float, metavar="SECONDS_PER_LAP",
                        help="observed new-compound pace gain")
    parser.add_argument("--wear-pace-cost", type=float, metavar="SECONDS_PER_PERCENT",
                        help="observed lap-time cost per tyre wear percentage point")
    parser.add_argument("--next-compound", type=int, metavar="CODE", help="planned next F1 22 compound code")
    parser.add_argument("--safety-car-pit-factor", type=float, metavar="0_TO_1",
                        help="calibrated safety-car pit loss divided by normal loss")
    parser.add_argument("--traffic-loss", type=float, metavar="SECONDS", help="estimated traffic cost per stop")
    parser.add_argument("--second-compound", type=int, metavar="CODE", help="enable modelled two-stop plans")
    parser.add_argument("--strategy-profile", choices=("conservative", "balanced", "aggressive"),
                        default="balanced")
    parser.add_argument("--dashboard", action="store_true", help="start optional loopback dashboard")
    parser.add_argument("--open-dashboard", action="store_true", help="open the local dashboard in a browser after it starts")
    parser.add_argument("--drive", action="store_true", help="start dashboard, browser, history and a new raw capture together")
    parser.add_argument("--auto-record-sessions", choices=("races", "weekend", "all-driving"),
                        default="races", help="automatic capture policy with --drive (default: races)")
    parser.add_argument("--bind-address", default=HOST, metavar="IPV4",
                        help="UDP listen address; default 127.0.0.1; use 0.0.0.0 only for remote senders")
    parser.add_argument("--sender-address", metavar="IPV4",
                        help="accept telemetry only from this sender IPv4 address")
    parser.add_argument("--performance-mode", action="store_true", help="reduce reports and disable dashboard")
    parser.add_argument("--voice-model", metavar="FOLDER", help="enable offline F8 voice using a Vosk model")
    parser.add_argument("--voice-input-device", type=int, metavar="INDEX", help="sounddevice microphone index")
    parser.add_argument("--voice-volume", type=float, default=0.8, metavar="0_TO_1")
    parser.add_argument("--no-history", action="store_true", help="disable structured SQLite history")
    parser.add_argument("--stop-file", metavar="NAME.stop", help="stop cleanly when control/NAME.stop is created")
    parser.add_argument("--llm-endpoint", metavar="URL", help="optional OpenAI-compatible chat completions endpoint")
    parser.add_argument("--llm-model", metavar="NAME", help="model used only to classify question intent")
    args = parser.parse_args(argv)
    if args.drive:
        if args.record or args.replay or args.debrief or args.export_history:
            parser.error("--drive starts a new capture and cannot be combined with another mode")
        if args.performance_mode or args.no_history:
            parser.error("--drive requires the dashboard and structured history")
        args.dashboard = True
        args.open_dashboard = True
    if args.open_dashboard and (not args.dashboard or args.performance_mode or args.replay
                                or args.debrief or args.export_history):
        parser.error("--open-dashboard requires a live --dashboard session")
    if args.auto_record_sessions != "races" and not args.drive:
        parser.error("--auto-record-sessions requires --drive")
    record_name = args.record
    try:
        alert_config = AlertConfig(cooldown_seconds=args.alert_cooldown)
        strategy = StrategyAssumptions(pit_loss_seconds=args.pit_loss,
                                       fresh_tyre_gain_seconds_per_lap=args.fresh_tyre_gain,
                                       wear_cost_seconds_per_percent=args.wear_pace_cost,
                                       next_compound_code=args.next_compound,
                                       safety_car_pit_loss_factor=args.safety_car_pit_factor,
                                       traffic_loss_seconds=args.traffic_loss,
                                       second_compound_code=args.second_compound,
                                       profile=args.strategy_profile)
    except ValueError as exc:
        parser.error(str(exc))
    if bool(args.llm_endpoint) != bool(args.llm_model):
        parser.error("--llm-endpoint and --llm-model must be supplied together")
    if not 0 <= args.voice_volume <= 1:
        parser.error("--voice-volume must be between zero and one")
    if args.debrief or args.export_history:
        try:
            with RaceHistory(DEFAULT_DB) as history:
                if args.debrief:
                    print(json.dumps(history.debrief(args.debrief), indent=2))
                else:
                    uid, fmt, path = args.export_history
                    history.export(uid, path, fmt)
                    print(f"Exported session {uid} to {path}")
            return 0
        except (OSError, ValueError) as exc:
            print(f"History error: {exc}", file=sys.stderr)
            return 1
    try:
        handler = create_log_handler()
    except OSError as exc:
        print(f"Cannot create diagnostic log: {exc}. Receiver not started.", file=sys.stderr)
        return 1

    previous_level, previous_propagate = LOGGER.level, LOGGER.propagate
    LOGGER.setLevel("INFO")
    LOGGER.propagate = False
    LOGGER.addHandler(handler)
    try:
        print(f"Diagnostic log: {handler.baseFilename}", flush=True)
        LOGGER.info("Companion starting; mode=%s.", "replay" if args.replay else "receive")
        try:
            if args.replay:
                replay_capture(sys.stdout, args.replay, alert_config=alert_config,
                               strategy_assumptions=strategy)
            else:
                run_receiver(sys.stdout, record_name=record_name, alert_config=alert_config, strategy_assumptions=strategy,
                             history_path=None if args.no_history else str(DEFAULT_DB),
                             dashboard=args.dashboard, open_dashboard=args.open_dashboard,
                             performance_mode=args.performance_mode,
                             voice_model=args.voice_model, voice_input_device=args.voice_input_device,
                             voice_volume=args.voice_volume, llm_endpoint=args.llm_endpoint,
                             llm_model=args.llm_model, stop_name=args.stop_file,
                             auto_record=args.drive, bind_address=args.bind_address,
                             sender_address=args.sender_address, recording_policy=args.auto_record_sessions)
        except KeyboardInterrupt:
            LOGGER.info("Receiver stopped by user; socket closed.")
            print("\nReceiver stopped; socket closed.")
            return 0
        except (OSError, ValueError) as exc:
            LOGGER.exception("Receiver or capture failed.")
            print(
                f"Receiver or capture error: {exc}. "
                f"For UDP binding, check {args.bind_address}:{PORT}, port conflicts and local firewall settings.",
                file=sys.stderr,
            )
            return 1
        except Exception:
            LOGGER.exception("Unexpected companion failure.")
            raise
        LOGGER.info("Receiver stopped; socket closed.")
        return 0
    finally:
        LOGGER.removeHandler(handler)
        handler.close()
        LOGGER.setLevel(previous_level)
        LOGGER.propagate = previous_propagate


if __name__ == "__main__":
    raise SystemExit(main())

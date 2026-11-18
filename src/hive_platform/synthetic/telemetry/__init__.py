"""S6: synthetic hive telemetry, as exported by a (simulated) IoT platform.

Module responsibilities:
    config    settings, input/output paths, stats (no logic)
    signals   healthy hive signals driven by real weather (pure functions)
    faults    SWARM / DRIFT drawing and injection, combined ground-truth fault log
    generate  one hive, one season, split across the sensors it carried
    pipeline  all hives in batches, validation, monthly files, manifest, CLI
"""

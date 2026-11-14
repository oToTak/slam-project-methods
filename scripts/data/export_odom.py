#!/usr/bin/env python3
"""Export the wheel odometry (/odom) of one UCU sequence to TUM text (TASK_all_methods.md T0.2).

The official exporter (ucu-slam-dataset scripts/export_tum.py, pinned) does not write /odom, so this is a separate step
that does NOT touch it and uses the same conventions: timestamp = bag record time in seconds with 9 decimals,
line = 'timestamp x y z qx qy qz qw', header '#timestamp x y z qx qy qz qw'. Only /odom messages are decoded, so it is fast.

Usage (needs rosbags: the slam venv has it):
    python scripts/data/export_odom.py <dir with the .mcap> <output odom.txt>
Writes to <output>.partial and renames it at the end, so a half-written odom.txt never exists.
Exit codes: 0 ok, 1 no /odom messages, 2 bad usage / no bag.
"""
import sys
from pathlib import Path

TOPIC = "/odom"
HEADER = "#timestamp x y z qx qy qz qw"


def format_line(timestamp_ns, pose):
    """pose = (x, y, z, qx, qy, qz, qw)."""
    return f"{timestamp_ns / 1_000_000_000:.9f} " + " ".join(str(float(v)) for v in pose)


def export(bag_dir, out_path):
    from rosbags.rosbag2 import Reader
    from rosbags.typesys import Stores, get_typestore
    typestore = get_typestore(Stores.ROS2_JAZZY)  # same as the official exporter
    out_path = Path(out_path)
    tmp = out_path.with_name(out_path.name + ".partial")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with Reader(bag_dir) as reader, open(tmp, "w") as f:
        conns = [c for c in reader.connections if c.topic == TOPIC]
        if not conns:
            tmp.unlink(missing_ok=True)
            return 0
        f.write(HEADER + "\n")
        for conn, ts, raw in reader.messages(connections=conns):
            p = typestore.deserialize_cdr(raw, conn.msgtype).pose.pose
            f.write(format_line(ts, (p.position.x, p.position.y, p.position.z,
                                     p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w)) + "\n")
            n += 1
    tmp.replace(out_path)
    return n


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    bag_dir, out_path = Path(argv[1]), Path(argv[2])
    if not bag_dir.is_dir():
        print(f"no such directory: {bag_dir}", file=sys.stderr)
        return 2
    n = export(bag_dir, out_path)
    if n == 0:
        print(f"no {TOPIC} messages in {bag_dir}", file=sys.stderr)
        return 1
    print(f"wrote {out_path} ({n} poses)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

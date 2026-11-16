"""Shared pure-python helpers of the ORB-SLAM3 integration (stdlib only: no numpy, runs in any venv).

* SE(3) / quaternion maths on plain lists (poses are 'x y z qx qy qz qw', rotation matrices are 3x3 lists)
* calibration loading (configs/orbslam3/ucu_calib.json)
* rgb.txt parsing that keeps the original timestamp strings
"""
import json
import math
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CALIB = REPO_ROOT / "configs" / "orbslam3" / "ucu_calib.json"


# ------------------------------------------------------------------ calibration

def load_calibration(path=DEFAULT_CALIB):
    cal = json.loads(Path(path).read_text(encoding="utf-8"))
    T = cal["T_cam_imu"]
    if len(T) != 4 or any(len(r) != 4 for r in T):
        raise ValueError(f"{path}: T_cam_imu must be 4x4")
    return cal


# ------------------------------------------------------------------ small linear algebra

def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))] for i in range(len(a))]


def transpose(a):
    return [list(r) for r in zip(*a)]


def rot_of(T):
    return [r[:3] for r in T[:3]]


def trans_of(T):
    return [T[0][3], T[1][3], T[2][3]]


def make_se3(R, t):
    return [list(R[0]) + [t[0]], list(R[1]) + [t[1]], list(R[2]) + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def se3_inverse(T):
    """Inverse of a rigid transform: [R t]^-1 = [R^T  -R^T t]."""
    Rt = transpose(rot_of(T))
    t = trans_of(T)
    ti = [-sum(Rt[i][k] * t[k] for k in range(3)) for i in range(3)]
    return make_se3(Rt, ti)


def is_rotation(R, tol=1e-6):
    RtR = mat_mul(transpose(R), R)
    ok = all(abs(RtR[i][j] - (1.0 if i == j else 0.0)) < tol for i in range(3) for j in range(3))
    det = (R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
           - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
           + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0]))
    return ok and abs(det - 1.0) < tol


# ------------------------------------------------------------------ quaternions (x, y, z, w)

def quat_normalize(q):
    n = math.sqrt(sum(v * v for v in q))
    if not n > 0.0 or not math.isfinite(n):
        raise ValueError("zero or non-finite quaternion")
    return [v / n for v in q]


def quat_to_rot(q):
    x, y, z, w = quat_normalize(q)
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def rot_to_quat(R):
    """Rotation matrix -> unit quaternion (x, y, z, w), w >= 0 (Shepperd's method, numerically stable)."""
    tr = R[0][0] + R[1][1] + R[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (R[2][1] - R[1][2]) / s, (R[0][2] - R[2][0]) / s, (R[1][0] - R[0][1]) / s
    elif R[0][0] > R[1][1] and R[0][0] > R[2][2]:
        s = math.sqrt(1.0 + R[0][0] - R[1][1] - R[2][2]) * 2
        w, x, y, z = (R[2][1] - R[1][2]) / s, 0.25 * s, (R[0][1] + R[1][0]) / s, (R[0][2] + R[2][0]) / s
    elif R[1][1] > R[2][2]:
        s = math.sqrt(1.0 + R[1][1] - R[0][0] - R[2][2]) * 2
        w, x, y, z = (R[0][2] - R[2][0]) / s, (R[0][1] + R[1][0]) / s, 0.25 * s, (R[1][2] + R[2][1]) / s
    else:
        s = math.sqrt(1.0 + R[2][2] - R[0][0] - R[1][1]) * 2
        w, x, y, z = (R[1][0] - R[0][1]) / s, (R[0][2] + R[2][0]) / s, (R[1][2] + R[2][1]) / s, 0.25 * s
    q = quat_normalize([x, y, z, w])
    return [-v for v in q] if q[3] < 0 else q


def pose_to_mat(pose):
    """(x, y, z, qx, qy, qz, qw) -> 4x4."""
    return make_se3(quat_to_rot(pose[3:7]), pose[0:3])


def mat_to_pose(T):
    return trans_of(T) + rot_to_quat(rot_of(T))


# ------------------------------------------------------------------ text helpers

def fmt_real(x):
    """Float text that OpenCV's YAML reader always takes as a real number (ORB-SLAM3 rejects '3' or '1e-05' for float keys)."""
    if not math.isfinite(float(x)):
        raise ValueError(f"non-finite number {x!r} cannot go into a settings file")
    s = repr(float(x))
    if "e" in s:                      # python repr: '1e-05', '3.27e-05', '1e+16'
        mant, exp = s.split("e")
        if "." not in mant:
            mant += ".0"
        return f"{mant}e{exp}"
    if "." not in s:
        s += ".0"
    return s


def ts_9dec(token):
    """Timestamp token -> exactly 9 decimals without going through binary floating point."""
    return format(Decimal(token), ".9f")


def read_rgb_txt(path):
    """TUM rgb.txt -> list of (timestamp_token, timestamp_float, relative_path). '#' and blank lines skipped."""
    out = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"{path}: bad line {line!r}")
        out.append((parts[0], float(parts[0]), parts[1]))
    return out

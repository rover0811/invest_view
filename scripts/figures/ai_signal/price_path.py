import random

KEYPOINTS = [
    (540, 0.3), (544, 0.7), (548, 0.1), (552, -0.3), (556, 0.2), (560, 0.6), (563, 0.85), (565, 1.25),
    (570, 1.9), (575, 1.4), (582, 2.1), (588, 1.6), (594, 2.0), (600, 1.5), (604, 4.8),
    (610, 3.6), (615, 4.1), (622, 3.0), (628, 3.6), (636, 2.4), (644, 2.9), (652, 1.9), (660, 2.3),
    (668, 1.3), (672, 0.85), (675, 0.8), (682, 0.95), (690, 0.65), (696, 0.92), (702, 0.75),
    (708, 0.6), (712, 0.45), (718, 0.1), (724, 0.4), (730, -0.1), (736, 0.2), (742, -0.6),
    (746, -0.85), (748, -1.1), (752, -1.4), (758, -1.8), (764, -1.2), (770, -1.6), (778, -1.0),
    (786, -1.5), (796, -2.3), (806, -1.8), (816, -2.2), (828, -1.6), (840, -2.1), (852, -2.7),
    (864, -2.2), (876, -2.5), (888, -1.9), (900, -2.3), (912, -1.8), (922, -2.2), (930, -2.1),
]


def bridge(n, amp, rng):
    walk = [0.0]
    for _ in range(n):
        walk.append(walk[-1] + rng.gauss(0, amp))
    return [w - walk[-1] * i / n for i, w in enumerate(walk)]


def make_path(seed, amp=0.09):
    rng = random.Random(seed)
    path = {}
    for (m0, p0), (m1, p1) in zip(KEYPOINTS, KEYPOINTS[1:]):
        b = bridge(m1 - m0, amp, rng)
        for i, m in enumerate(range(m0, m1)):
            path[m] = p0 + (p1 - p0) * i / (m1 - m0) + b[i]
    path[930] = KEYPOINTS[-1][1]
    return path


def transitions(path):
    st, out = None, []
    for m in range(540, 931):
        p, ns = path[m], st
        if st in (None, "NEU"):
            ns = "POS" if p >= 1 else ("NEG" if p <= -1 else st)
        elif st == "POS" and p < 0.5:
            ns = "NEU" if p > -1 else "NEG"
        elif st == "NEG" and p > -0.5:
            ns = "NEU" if p < 1 else "POS"
        if ns != st:
            out.append((m, ns))
            st = ns
    return out


if __name__ == "__main__":
    for seed in range(2000):
        p = make_path(seed)
        if transitions(p) == [(564, "POS"), (712, "NEU"), (748, "NEG")] and 0.5 < p[675] < 1:
            print("seed", seed)
            break

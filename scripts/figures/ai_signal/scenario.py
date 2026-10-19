"""가상 시나리오: 하루 동안 방향별 윈도우의 사건으로 시그널 문구가 바뀌는 과정을 GIF로 그린다."""
import textwrap

import matplotlib
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle

FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
font_manager.fontManager.addfont(FONT)
matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
matplotlib.rcParams["axes.unicode_minus"] = False

INK, MUTED, META, GRID = "#1A1A18", "#6B6A67", "#B0AEA9", "#E6E4DF"
UP, DOWN = "#D6453D", "#2F6FB3"
STATE_BG = {"POS": "#FBECEB", "NEU": "#F2F1EE", "NEG": "#EAF1F9"}
STATE_NAME = {"POS": "긍정", "NEU": "중립", "NEG": "부정", None: "없음"}
LANE_Y = {"POS": 3, "NEU": 2, "NEG": 1, "OUT": 0}
NAME = "A반도체"

DAY_START, OPEN, CLOSE, DAY_END = 7 * 60 + 20, 9 * 60, 15 * 60 + 30, 15 * 60 + 40


def hm(m):
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


from price_path import make_path

path = make_path(seed=1)

EVENTS = {
    "hbm": dict(x=450, known=450, lane="POS", shape="o", text="HBM 공급 계약 기대"),
    "wait": dict(x=480, known=480, lane="NEU", shape="o", text="실적 발표 앞두고 관망"),
    "hbm_dup": dict(dy=-0.27, x=500, known=500, lane="POS", shape="o", text="같은 제목 3건을 1건으로 합침", dup=3),
    "listing": dict(x=510, known=510, lane="OUT", shape="x", text="시세 나열 기사 제외"),
    "dram_old": dict(x=520, known=520, lane="NEG", shape="o", text="중국 D램 증산 가능성"),
    "hbm_deal": dict(x=603, known=606, lane="POS", shape="o", text="대형 고객사와 HBM 장기 공급 계약"),
    "tp": dict(dy=-0.27, x=612, known=608, lane="POS", shape="s", text="B증권 목표가 상향"),
    "flow_org": dict(x=632, known=615, lane="POS", shape="^", text="기관 순매수", z=[(615, 2.1), (675, 1.4)]),
    "flow_frgn": dict(x=632, known=615, lane="NEG", shape="^", text="외국인 순매도",
                      z=[(615, -0.3), (675, -1.2), (795, -1.9), (930, -2.0)]),
    "listing2": dict(x=700, known=700, lane="OUT", shape="x", text=""),
    "mkt_frgn": dict(dy=-0.27, x=778, known=770, lane="NEG", shape="D", text="외국인 코스피 순매도 확대 (시장 범위)"),
    "dram_late": dict(x=760, known=772, lane="NEG", shape="o", text="중국 D램 증산 공식 발표 (후행)"),
    "listing3": dict(x=850, known=850, lane="OUT", shape="x", text=""),
}

VERSIONS = [
    (564, "v1", "SHOWN", "대체 문구: 수익률 분해 (LLM 오류, 재시도 등록)", "반도체 업종 전반 상승", [], "POS"),
    (580, "v2", "SHOWN", "LLM (재호출 성공)", "HBM 공급 계약 기대",
     [(1, ["hbm", "hbm_dup"], "HBM 공급 계약 확대 기대가 커지면서 매수세가 몰렸어요.")], "POS"),
    (607, "v3", "SHOWN", "LLM (새 사건으로 다시 생성)", "HBM 장기 공급 계약 체결",
     [(1, ["hbm", "hbm_dup", "hbm_deal"], "대형 고객사와 HBM 장기 공급 계약을 맺었다는 소식에 매수세가 크게 몰렸어요.")], "POS"),
    (614, "v4", "SHOWN", "LLM (새 사건으로 다시 생성)", "HBM 장기 공급 계약 체결",
     [(1, ["hbm", "hbm_dup", "hbm_deal"], "대형 고객사와 HBM 장기 공급 계약을 맺었다는 소식에 매수세가 크게 몰렸어요."),
      (2, ["tp"], "B증권이 목표주가를 올리면서 상승 폭이 커졌어요.")], "POS"),
    (617, "v5", "SHOWN", "LLM (수급 갱신으로 다시 생성)", "HBM 장기 공급 계약 체결",
     [(1, ["hbm", "hbm_dup", "hbm_deal"], "대형 고객사와 HBM 장기 공급 계약을 맺었다는 소식에 매수세가 크게 몰렸어요."),
      (2, ["tp"], "B증권이 목표주가를 올리면서 상승 폭이 커졌어요."),
      (3, ["flow_org"], "기관이 평소보다 많이 사들였어요.")], "POS"),
    (712, "v6", "HIDDEN", "방향이 바뀌어 문구를 내렸어요", None, [], "NEU"),
    (715, "v7", "SHOWN", "LLM", "실적 발표 앞둔 관망세",
     [(1, ["wait"], "실적 발표를 앞두고 관망하는 분위기가 이어졌어요.")], "NEU"),
    (748, "v8", "HIDDEN", "방향이 바뀌어 문구를 내렸어요", None, [], "NEG"),
    (751, "v9", "SHOWN", "대체 문구: 수급 (LLM 오류, 재시도 등록)", "외국인 매도세",
     [(1, ["flow_frgn"], "외국인 순매도가 이어졌어요.")], "NEG"),
    (785, "v10", "SHOWN", "LLM (재호출 성공)", "중국 D램 증산 우려",
     [(1, ["dram_old", "dram_late"], "중국 업체의 D램 증산 발표로 공급 과잉 우려가 커졌어요."),
      (2, ["mkt_frgn"], "외국인이 코스피 전반에서 순매도를 늘렸어요."),
      (3, ["flow_frgn"], "외국인 매도가 이 종목에도 집중됐어요.")], "NEG"),
    (795, "v11", "SHOWN", "LLM (수급 갱신으로 다시 생성)", "중국 D램 증산 우려",
     [(1, ["dram_old", "dram_late"], "중국 업체의 D램 증산 발표로 공급 과잉 우려가 커졌어요."),
      (2, ["mkt_frgn"], "외국인이 코스피 전반에서 순매도를 늘렸어요."),
      (3, ["flow_frgn"], "외국인 매도가 이 종목에도 집중됐어요.")], "NEG"),
    (930, "v12", "SHOWN", "LLM (장 마감 재생성)", "중국 D램 증산 우려",
     [(1, ["dram_old", "dram_late"], "중국 업체의 D램 증산 발표로 공급 과잉 우려가 커졌어요."),
      (2, ["mkt_frgn"], "외국인이 코스피 전반에서 순매도를 늘렸어요."),
      (3, ["flow_frgn"], "외국인 매도가 이 종목에도 집중됐어요.")], "NEG"),
]

LOG = [
    (450, "장 시작 전 기사가 방향별 윈도우에 쌓입니다"),
    (500, "같은 제목 3건을 1건으로 합칩니다"),
    (510, "시세 나열 기사는 제외합니다"),
    (540, "09:00 장 시작. 등락률이 1%를 넘지 않아 시그널이 없습니다"),
    (564, "+1% 돌파: DAILY move 생성, 긍정 윈도우로 생성 요청"),
    (564, "LLM 오류: 수익률 분해로 대체 문구 발행, 재시도 등록"),
    (580, "LLM 재호출 성공: 긍정 윈도우의 사건으로 문구 교체"),
    (605, "5분 3% 급변: INTRADAY_SPIKE move 생성"),
    (607, "큰 호재 기사 도착: 긍정 윈도우를 다시 판단, 문구 교체"),
    (614, "목표가 상향 이벤트 도착: 원인 2개로 갱신"),
    (617, "수급 추정 1회차: 기관 z +2.1, 원인 3번으로 추가"),
    (675, "+0.8%: 0.5%와 1% 사이라 긍정 상태를 유지합니다"),
    (675, "수급 추정 2회차: 이전 행을 닫고 새 값으로 갱신"),
    (712, "+0.5% 아래: 중립 전환, HIDDEN 발행"),
    (715, "중립 윈도우의 기사로 문구 생성"),
    (748, "-1% 아래: 부정 전환, HIDDEN 발행"),
    (751, "LLM 오류: 수급으로 대체 문구 발행, 재시도 등록"),
    (770, "시장 범위 사건 도착: 열린 move가 있는 종목에 요청"),
    (772, "후행 기사 도착: 급변 뒤에 알려진 기사도 후보입니다"),
    (785, "LLM 재호출 성공: 같은 사건 기사 2건을 원인 1번으로 묶음"),
    (795, "수급 추정 3회차: 외국인 z -1.9로 갱신, 다시 생성"),
    (930, "장 마감: 확정치로 다시 생성"),
]

FRAME_START = 520


def state_at(m):
    if m < 564:
        return None
    if m < 712:
        return "POS"
    if m < 748:
        return "NEU"
    return "NEG"


def version_at(m):
    cur = None
    for v in VERSIONS:
        if m >= v[0]:
            cur = v
    return cur


def z_at(ev, m):
    cur = None
    for t, z in ev.get("z", []):
        if m >= t:
            cur = z
    return cur


fig = plt.figure(figsize=(13, 7.3), dpi=120, facecolor="white")
gs = fig.add_gridspec(2, 2, width_ratios=[2.2, 1], height_ratios=[1.15, 1],
                      left=0.075, right=0.985, top=0.80, bottom=0.075, wspace=0.07, hspace=0.12)
ax_px = fig.add_subplot(gs[0, 0])
ax_ev = fig.add_subplot(gs[1, 0], sharex=ax_px)
ax_cd = fig.add_subplot(gs[:, 1])
fig.suptitle("하루 동안 시그널 문구가 바뀌는 과정", x=0.075, y=0.975, ha="left", fontsize=13,
             color=MUTED, fontweight="bold")
fig.text(0.075, 0.012, "가격 상태에 맞는 윈도우(긍정, 중립, 부정)의 사건만 골라 문구를 만듭니다. "
         "종목, 가격, 기사는 설명을 위해 만든 예시입니다.", fontsize=8.5, color=META)
scene_title = fig.text(0.075, 0.885, "", fontsize=19, fontweight="bold", color=INK)
scene_desc = fig.text(0.075, 0.842, "", fontsize=11, color=MUTED)

SCENES = [
    (DAY_START, None, "장 시작 전", "기사가 방향별 윈도우(긍정, 중립, 부정)에 미리 쌓입니다"),
    (564, "POS", "1/9  긍정 진입: LLM 오류로 대체 문구 발행",
     "+1%를 넘어 시그널을 만들었지만 LLM 호출이 실패해, 수익률 분해로 만든 문구를 먼저 보여 줍니다"),
    (580, "POS", "2/9  LLM 재호출 성공: 대체 문구를 교체",
     "실패한 LLM 호출을 다시 시도해, 긍정 윈도우의 최신 사건으로 문구를 만들었습니다"),
    (607, "POS", "3/9  새 사건 도착: 문구를 다시 생성",
     "상태가 같아도 윈도우에 큰 사건이 들어오면 다시 판단합니다. 같은 사건의 기사들은 원인 1번으로 묶입니다"),
    (675, "POS", "4/9  +0.8%: 긍정 상태 유지",
     "+0.5%와 +1% 사이에서는 상태를 바꾸지 않습니다. 경계에서 문구가 계속 바뀌는 것을 막습니다"),
    (712, "NEU", "5/9  중립 전환: 문구를 먼저 내림",
     "+0.5% 아래로 내려가 방향이 바뀌었습니다. 이전 문구가 남지 않도록 HIDDEN을 먼저 발행합니다"),
    (715, "NEU", "6/9  중립 윈도우의 사건으로 문구 생성",
     "중립 상태에서는 중립 윈도우의 사건만 후보로 씁니다"),
    (748, "NEG", "7/9  부정 전환: 문구를 먼저 내림",
     "-1% 아래로 내려가 방향이 다시 바뀌었습니다. HIDDEN을 먼저 발행합니다"),
    (751, "NEG", "8/9  LLM 오류: 수급으로 대체 문구 발행",
     "LLM 호출이 실패하면 같은 방향의 수급으로 문구를 만들고, 재호출을 등록합니다"),
    (785, "NEG", "9/9  LLM 재호출 성공: 같은 사건을 하나의 원인으로 묶음",
     "기사 2건을 원인 1번으로 묶었습니다. 급변 뒤에 알려진 기사와 시장 범위 사건도 후보입니다"),
    (930, "NEG", "장 마감: 확정치로 다시 생성", "장 마감 뒤 확정된 수급으로 시그널을 한 번 더 만듭니다"),
]


def draw_scene(now):
    cur = SCENES[0]
    for sc in SCENES:
        if now >= sc[0]:
            cur = sc
    _, st, title, desc = cur
    scene_title.set_text(title)
    scene_title.set_color({"POS": UP, "NEG": DOWN, "NEU": INK}.get(st, MUTED))
    scene_desc.set_text(desc)


def style(ax):
    ax.set_xlim(DAY_START, DAY_END)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(META)
    ax.tick_params(colors=MUTED, labelsize=8)
    ticks = [h * 60 for h in range(8, 16)]
    ax.set_xticks(ticks)
    ax.set_xticklabels([hm(t) for t in ticks])


SCENE_MARKS = [
    (564, "1 긍정 진입", -16, 1.4, "POS"),
    (580, "2 LLM 재호출", -6, 2.3, "POS"),
    (607, "3 새 사건으로 갱신", 28, -2.4, "POS"),
    (675, "4 상태 유지", 8, -1.1, "POS"),
    (712, "5 중립 전환", -14, 1.5, "NEU"),
    (715, "6 중립 문구", 8, -1.3, "NEU"),
    (748, "7 부정 전환", 4, 1.4, "NEG"),
    (751, "8 수급 대체 문구", 10, -1.3, "NEG"),
    (785, "9 원인 묶음", 6, 1.3, "NEG"),
]


def draw_scene_marks(ax, now):
    for minute, text, dx, dy, st in SCENE_MARKS:
        if now < minute:
            continue
        y = path[minute]
        col = {"POS": UP, "NEG": DOWN}.get(st, INK)
        ax.plot(minute, y, "o", ms=4.5, color=col, zorder=6)
        ax.annotate(text, xy=(minute, y), xytext=(minute + dx, y + dy), fontsize=7.5, color=col,
                    fontweight="bold", ha="center", zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85),
                    arrowprops=dict(arrowstyle="-", color=col, lw=0.7))


def draw_price(now):
    ax = ax_px
    ax.clear()
    style(ax)
    ax.set_ylim(-3.2, 5.6)
    ax.set_ylabel("전일 종가 대비 (%)", color=MUTED, fontsize=9)
    for t0, t1, st in ((564, 712, "POS"), (712, 748, "NEU"), (748, CLOSE, "NEG")):
        if now > t0:
            ax.axvspan(t0, min(now, t1), color=STATE_BG[st], zorder=0)
    for y, ls in ((1, "-"), (-1, "-"), (0.5, "--"), (-0.5, "--")):
        ax.axhline(y, color=META, lw=0.8, ls=ls, zorder=1)
    ax.axhline(0, color=GRID, lw=0.8)
    ax.text(DAY_START + 3, 1.15, "+1% 긍정 진입", fontsize=7, color=META)
    ax.text(DAY_START + 3, 0.6, "+0.5% 긍정 이탈", fontsize=7, color=META)
    ax.text(DAY_START + 3, -1.45, "-1% 부정 진입", fontsize=7, color=META)
    for b in range(OPEN, min(int(now), CLOSE), 5):
        ms = [m for m in range(b, min(b + 5, int(now) + 1)) if m in path]
        if not ms:
            continue
        vals = [path[m] for m in ms]
        o, c, hi, lo = vals[0], vals[-1], max(vals), min(vals)
        col = UP if c >= o else DOWN
        ax.plot([b + 2.5, b + 2.5], [lo, hi], color=col, lw=0.8, zorder=3)
        ax.add_patch(Rectangle((b + 0.6, min(o, c)), 3.8, max(abs(c - o), 0.04), color=col, zorder=4))
    draw_scene_marks(ax, now)
    if now >= 605:
        ax.annotate("5분 3% 급변", xy=(604, 4.8), xytext=(588, 5.3), fontsize=7.5, color=UP,
                    arrowprops=dict(arrowstyle="-", color=UP, lw=0.8))
    st = state_at(now)
    ax.text(DAY_END, 5.2, f"가격 상태: {STATE_NAME[st]}", ha="right", fontsize=10, fontweight="bold",
            color={"POS": UP, "NEG": DOWN}.get(st, MUTED))
    ax.axvline(now, color=INK, lw=0.8, alpha=0.5)
    plt.setp(ax.get_xticklabels(), visible=False)


def draw_events(now):
    ax = ax_ev
    ax.clear()
    style(ax)
    ax.set_ylim(-0.6, 3.6)
    ax.set_yticks([3, 2, 1, 0])
    ax.set_yticklabels(["긍정 윈도우", "중립 윈도우", "부정 윈도우", "제외"], fontsize=8.5)
    st = state_at(now)
    if st:
        y = LANE_Y[st]
        ax.add_patch(Rectangle((DAY_START, y - 0.45), DAY_END - DAY_START, 0.9,
                               fc=STATE_BG[st], ec={"POS": UP, "NEU": META, "NEG": DOWN}[st], lw=1.2, zorder=0))
    for y in (2.5, 1.5, 0.5):
        ax.axhline(y, color=GRID, lw=0.6)
    ver = version_at(now)
    badges = {}
    if ver:
        for rank, ids, _ in ver[5]:
            for i in ids:
                badges[i] = rank
    for key, ev in EVENTS.items():
        if now < ev["known"]:
            continue
        y = LANE_Y[ev["lane"]]
        active = st == ev["lane"]
        if ev["shape"] == "x":
            ax.plot(ev["x"], y, "x", color=META, ms=6, mew=1.3)
            if ev["text"]:
                ax.text(ev["x"] + 4, y - 0.05, ev["text"], fontsize=7, color=META, va="center")
            continue
        col = {"POS": UP, "NEU": MUTED, "NEG": DOWN}[ev["lane"]]
        ax.plot(ev["x"], y, ev["shape"], color=col if active or st is None else META, ms=8,
                alpha=1 if active or st is None else 0.55, zorder=5)
        z = z_at(ev, now)
        label = ev["text"] + (f" z {z:+.1f}" if z is not None else "")
        ax.text(ev["x"] + 5, y + ev.get("dy", 0.25), label, fontsize=7.2,
                color=INK if active or st is None else META, va="center")
        if key in badges:
            ax.text(ev["x"] - 7, y, str(badges[key]), fontsize=7, color="white", ha="center", va="center",
                    fontweight="bold", bbox=dict(boxstyle="circle,pad=0.25", fc=INK, ec="none"), zorder=6)
    handles = [plt.Line2D([], [], marker=mk, ls="", color=MUTED, label=lb, ms=6)
               for mk, lb in (("o", "뉴스"), ("s", "목표가"), ("^", "수급 (z 기록)"), ("D", "시장 범위"), ("x", "제외"))]
    ax.legend(handles=handles, ncol=5, fontsize=7.5, frameon=False, loc="lower left",
              bbox_to_anchor=(0, 1.0), handletextpad=0.3, columnspacing=1.2, labelcolor=MUTED)
    ax.axvline(now, color=INK, lw=0.8, alpha=0.5)


def euro(word):
    code = ord(word[-1]) - 0xAC00
    if not 0 <= code < 11172:
        return "으로"
    final = code % 28
    return "로" if final in (0, 8) else "으로"


def wrap(text, n=24):
    return textwrap.fill(text, n)


def draw_card(now):
    ax = ax_cd
    ax.clear()
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.add_patch(Rectangle((0.02, 0.285), 0.96, 0.695, fill=False, ec=META, lw=1))
    ax.text(0.07, 0.94, hm(now), fontsize=9, color=MUTED)
    pct = path.get(min(int(now), CLOSE), path[930]) if now >= OPEN else None
    ver = version_at(now)
    if pct is None or ver is None:
        ax.text(0.07, 0.86, NAME, fontsize=14, color=INK, fontweight="bold")
        ax.text(0.07, 0.79, "등락률이 1%를 넘지 않아\n시그널이 없습니다", fontsize=10, color=MUTED, va="top",
                linespacing=1.6)
    else:
        col = UP if pct >= 0 else DOWN
        ax.text(0.07, 0.86, f"{NAME}  {pct:+.1f}%", fontsize=14, color=col, fontweight="bold")
        _, vname, status, by, label, causes, _ = ver
        if status == "HIDDEN":
            ax.text(0.07, 0.79, "HIDDEN", fontsize=13, color=META, fontweight="bold", va="top")
            ax.text(0.07, 0.72, "방향이 바뀌어 문구를 내렸어요.\n새 윈도우의 사건으로 다시 만드는 중이에요.",
                    fontsize=9, color=MUTED, va="top", linespacing=1.6)
        else:
            word = "상승" if pct >= 0 else "하락"
            ax.text(0.07, 0.79, f"{label}{euro(label)}\n{NAME} {abs(pct):.1f}% {word}", fontsize=12, color=INK,
                    va="top", fontweight="bold", linespacing=1.5)
            y = 0.665
            for rank, _, text in causes:
                ax.text(0.085, y - 0.008, str(rank), fontsize=7, color="white", ha="center", va="center",
                        fontweight="bold", bbox=dict(boxstyle="circle,pad=0.25", fc=INK, ec="none"))
                w = wrap(text)
                ax.text(0.13, y, w, fontsize=8.3, color=INK, va="top", linespacing=1.45)
                y -= 0.028 * (w.count("\n") + 1) + 0.02
        ax.text(0.07, 0.345, f"{vname}  {status}", fontsize=9.5, color=INK, fontweight="bold")
        ax.text(0.07, 0.328, f"생성: {by}", fontsize=7.8, color=MUTED, va="top")
    ax.text(0.03, 0.25, "처리 기록", fontsize=9, color=MUTED, fontweight="bold")
    lines = [f"{hm(t)}  {msg}" for t, msg in LOG if t <= now][-8:]
    for i, line in enumerate(lines):
        ax.text(0.03, 0.215 - i * 0.027, line, fontsize=7.6, color=INK if i == len(lines) - 1 else MUTED)


def draw(now):
    draw_scene(now)
    draw_price(now)
    draw_events(now)
    draw_card(now)
    return []


SCHEDULE = [
    (564, 1.0), (566, 1.9),
    (580, 0.3), (582, 1.7),
    (607, 0.5), (609, 2.0),
    (675, 1.0), (677, 1.5),
    (712, 0.5), (713, 1.1),
    (715, 0.2), (717, 1.1),
    (748, 0.5), (749, 0.9),
    (751, 0.2), (753, 1.5),
    (785, 0.9), (787, 2.4),
    (DAY_END, 0.5), (DAY_END, 0.3),
]


def build_timeline(fps):
    keys, t = [(0.0, FRAME_START)], 0.0
    for minute, seconds in SCHEDULE:
        t += seconds
        keys.append((t, minute))
    frames = []
    for i in range(int(round(t * fps))):
        now = i / fps
        for (t0, m0), (t1, m1) in zip(keys, keys[1:]):
            if t0 <= now <= t1:
                frames.append(m0 + (m1 - m0) * (now - t0) / max(t1 - t0, 1e-9))
                break
    return frames

import os, sys, io, re, wave, time, queue, threading, html
import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from groq import Groq
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QTextBrowser, QPushButton)

load_dotenv()
MODEL = os.getenv("MODEL", "openai/gpt-oss-120b")
STT_MODEL = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
DEVICE = os.getenv("AUDIO_DEVICE", "").strip()
THRESH = float(os.getenv("VAD_THRESHOLD", "0.012"))
SILENCE = float(os.getenv("SILENCE_SEC", "0.45"))
client = Groq()

SYSTEM = """You are a senior Hotel Revenue Management Executive and Excel expert, acting as a study coach.
The user speaks Hindi/Hinglish. ALWAYS answer in clear, simple English, whatever language the question is in.

Your knowledge:
- Hotel revenue: Occupancy, ADR, RevPAR, TRevPAR, GOPPAR, MPI/ARI/RGI (STR comp set), pickup and pace reports,
  forecasting, BAR and dynamic pricing, restrictions (MLOS, CTA/CTD), overbooking, channel mix, OTA commission,
  rate parity, market segmentation, group displacement analysis, events calendar, budgeting, cancellations/no-shows,
  upselling, daily/weekly routine of a Revenue Executive, reports to the GM, revenue meetings.
- Excel: formulas (SUMIFS, COUNTIFS, XLOOKUP, INDEX-MATCH, IF, TEXT, EOMONTH), pivot tables, charts, conditional
  formatting, data validation, Power Query, FORECAST.LINEAR/TREND, Goal Seek, what-if, dashboards, shortcuts.

Answer format:
1. One-line direct answer or definition.
2. "Steps:" a numbered list (max 7), each step short and actionable. For Excel, give exact formulas with cell
   references, e.g. =SUMIFS(D:D,A:A,"Jan"). For hotel metrics, give the formula, e.g. RevPAR = Room revenue / Rooms available.
3. "Example:" one small example with real numbers.
Keep it about 120-180 words unless the user asks for detail ("full", "detail", "poora", "step by step").
If the topic is not about revenue, hotels, Excel, finance or study, reply only: IGNORE"""

# ---------- keywords (group -> words, Devanagari variants bhi) ----------
G = {
 "occ": ["occupancy","ऑक्यूपेंसी","ओक्यूपेंसी","ऑक्युपेंसी","adr","revpar","रेवपार","hotel","होटल","rooms","revpar","trevpar","goppar","pickup","pace","ota","overbooking","comp set","rate parity","booking","बुकिंग","cancellation","no show","segment","channel","yield","length of stay","mlos","bar rate","dynamic pricing","upsell"],
 "rev": ["revenue","रेवेन्यू","रेवन्यू","रेवेनु","रिवेन्यू","sales","सेल्स","income","इनकम","turnover","pricing","arpu","mrr","arr"],
 "prof": ["profit","प्रॉफिट","प्रोफिट","margin","मार्जिन","ebitda","roi","मुनाफा","npv","irr"],
 "grow": ["growth","ग्रोथ","cagr","forecast","फोरकास्ट","trend","analysis","एनालिसिस","kpi","budget","बजट","variance","breakeven","break even"],
 "xl": ["excel","एक्सेल","एक्सल","formula","फॉर्मूला","फार्मूला","pivot","पिवट","vlookup","xlookup","sumif","sumifs","countif","chart"],
 "cost": ["cost","कॉस्ट","कोस्ट","expense","खर्च","loss","tax","gst","depreciation","cogs"],
}
COLOR = {"occ":"#ff6fd8","rev":"#ffb84d","prof":"#4ee0a1","grow":"#6db8ff","xl":"#c79bff","cost":"#ff7f8a"}
STUDY = ["accounting","finance","economics","statistics","demand","supply","elasticity","balance sheet","cash flow","gdp","inflation"]
lat = lambda w: re.fullmatch(r"[a-z0-9 ]+", w, re.I) is not None
RX = {g: re.compile("(" + "|".join(("(?<![a-z])"+re.escape(w)+"(?![a-z])") if lat(w) else re.escape(w) for w in ws) + ")", re.I)
      for g, ws in G.items()}

def matches(t):
    out = [m.group(0).lower() for rx in RX.values() for m in rx.finditer(t)]
    out += [w for w in STUDY if w in t.lower()]
    return list(dict.fromkeys(out))

def paint(t, dim=False):
    h = html.escape(t)
    for g, rx in RX.items():
        h = rx.sub(lambda m: f'<b style="color:{COLOR[g]}">{m.group(0)}</b>', h)
    return h

class Bridge(QObject):
    level = Signal(float); status = Signal(str); heard = Signal(str, list)
    turn = Signal(int, str, list); token = Signal(int, str); drop = Signal(int)
B = Bridge()
cur_id = 0

def wav_bytes(pcm, sr):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    return b.getvalue()

def answer(qid, q):
    try:
        s = client.chat.completions.create(model=MODEL, stream=True, temperature=0.3,
            max_completion_tokens=1400, reasoning_effort="low",
            messages=[{"role":"system","content":SYSTEM},{"role":"user","content":q}])
        first = True; buf = ""
        for ch in s:
            if qid != cur_id: return           # naya sawal aa gaya, purana rok do
            t = ch.choices[0].delta.content if ch.choices else None
            if not t: continue
            buf += t
            if first and len(buf) < 6 and "IGNORE".startswith(buf.strip()): continue
            first = False
            if buf.strip().startswith("IGNORE"): B.drop.emit(qid); return
            B.token.emit(qid, t if buf == t else t)
        if first and buf: B.token.emit(qid, buf)
    except Exception as e:
        B.token.emit(qid, "ERROR: " + str(e)[:250])

def stt_worker(seg_q):
    global cur_id
    while True:
        pcm, sr = seg_q.get()
        try:
            r = client.audio.transcriptions.create(
                file=("a.wav", wav_bytes(pcm, sr)), model=STT_MODEL, temperature=0,
                prompt="revenue, occupancy, ADR, RevPAR, TRevPAR, GOPPAR, pickup, pace, OTA, BAR, overbooking, comp set, Excel, XLOOKUP, SUMIFS, pivot table")
            text = (getattr(r, "text", r) or "").strip()
        except Exception as e:
            B.status.emit("STT error: " + str(e)[:80]); continue
        if not text: continue
        m = matches(text)
        B.heard.emit(text, m)
        if m:
            cur_id += 1
            B.turn.emit(cur_id, text, m)
            threading.Thread(target=answer, args=(cur_id, text), daemon=True).start()

def pick_device():
    if not DEVICE: return None
    if DEVICE.isdigit(): return int(DEVICE)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and DEVICE.lower() in d["name"].lower(): return i
    B.status.emit("Device nahi mila, default mic use ho raha hai"); return None

def listener():
    seg_q = queue.Queue(); threading.Thread(target=stt_worker, args=(seg_q,), daemon=True).start()
    dev = pick_device(); info = sd.query_devices(dev, "input"); sr = int(info["default_samplerate"])
    frames = queue.Queue(); FR = int(sr * 0.03)
    def cb(x, n, t, s): frames.put(x.mean(axis=1) if x.ndim > 1 else x[:, 0].copy())
    ch = 1 if info["max_input_channels"] >= 1 else 2
    B.status.emit(f"sun raha hu: {info['name']}")
    pre, buf, speech, last_voice, start = [], [], False, 0, 0
    with sd.InputStream(device=dev, channels=ch, samplerate=sr, blocksize=FR, dtype="float32", callback=cb):
        while True:
            f = frames.get(); rms = float(np.sqrt(np.mean(f * f))); B.level.emit(rms)
            now = time.time()
            if rms > THRESH:
                if not speech: speech, start, buf = True, now, list(pre)
                last_voice = now
            if speech:
                buf.append(f)
                if now - last_voice > SILENCE or now - start > 10:
                    if last_voice - start > 0.4: seg_q.put((np.concatenate(buf), sr))
                    speech, buf = False, []
            else:
                pre.append(f); pre = pre[-10:]

class Win(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(430, 680); self.move(40, 40); self.turns = []; self.drag = None
        self.setStyleSheet("QWidget#box{background:rgba(11,21,36,225);border-radius:16px}"
                           "QLabel{color:#8fa3bf;font-size:12px}"
                           "QTextBrowser{background:transparent;border:0;color:#e8eef7;font-size:15px}"
                           "QPushButton{background:#18283f;color:#e8eef7;border:0;border-radius:8px;padding:2px 9px}")
        box = QWidget(self); box.setObjectName("box")
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(box)
        v = QVBoxLayout(box); v.setContentsMargins(12, 8, 12, 10)
        top = QHBoxLayout(); self.st = QLabel("shuru ho raha hai..."); self.lv = QLabel("")
        x = QPushButton("x"); x.clicked.connect(QApplication.quit)
        top.addWidget(self.st, 1); top.addWidget(self.lv); top.addWidget(x); v.addLayout(top)
        self.feed = QTextBrowser(); self.log = QTextBrowser(); self.log.setFixedHeight(170)
        self.log.setStyleSheet("font-size:12px;color:#8fa3bf")
        v.addWidget(self.feed, 1); v.addWidget(self.log)
        B.level.connect(lambda r: self.lv.setText("▮" * min(12, int(r / THRESH * 4)) or "·"))
        B.status.connect(self.st.setText)
        B.heard.connect(self.on_heard); B.turn.connect(self.on_turn)
        B.token.connect(self.on_token); B.drop.connect(self.on_drop)

    def on_heard(self, t, m):
        tag = f'<b style="color:#4ee0a1">pakda: {html.escape(", ".join(m))}</b>' if m else "ignore (keyword nahi)"
        self.log.append(f'{time.strftime("%H:%M:%S")} {paint(t)} — {tag}')
    def on_turn(self, i, q, m): self.turns.append({"id": i, "q": q, "m": m, "a": ""}); self.render()
    def on_token(self, i, t):
        for x in self.turns:
            if x["id"] == i: x["a"] += t
        self.render()
    def on_drop(self, i): self.turns = [x for x in self.turns if x["id"] != i]; self.render()
    def render(self):
        out = []
        for n, x in enumerate(self.turns[-5:]):
            last = n == len(self.turns[-5:]) - 1
            c, sz = ("#e8eef7", 22) if last else ("#6f819b", 14)
            a = html.escape(x["a"]).replace("\n", "<br>") or "<i>soch raha hu…</i>"
            anchor = '<a name="latest"></a>' if last else ""
            out.append(f'<p style="color:{c};font-size:{sz}px"><b>{paint(x["q"])}</b></p>'
                       f'<p style="color:{c};font-size:{15 if last else 12}px">{anchor}{a}</p><hr>')
        self.feed.setHtml("".join(out)); self.feed.scrollToAnchor("latest")
    def mousePressEvent(self, e): self.drag = e.globalPosition().toPoint() - self.pos()
    def mouseMoveEvent(self, e):
        if self.drag: self.move(e.globalPosition().toPoint() - self.drag)
    def mouseReleaseEvent(self, e): self.drag = None

if __name__ == "__main__":
    if "--devices" in sys.argv:
        print(sd.query_devices()); sys.exit()
    app = QApplication(sys.argv); w = Win(); w.show()
    threading.Thread(target=listener, daemon=True).start()
    sys.exit(app.exec())

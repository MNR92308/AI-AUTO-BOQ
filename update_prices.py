#!/usr/bin/env python3
"""อัปเดต prices.json อัตโนมัติ: ให้ LLM (ผ่าน OpenRouter) ค้นเว็บหาราคาล่าสุด แล้วตรวจความสมเหตุสมผลก่อนบันทึก

ติดตั้ง : pip install openai
ตั้งค่า  : export OPENROUTER_API_KEY=sk-or-...
          export OPENROUTER_MODEL=anthropic/claude-sonnet-4.5   # ไม่บังคับ
รัน     : python update_prices.py
ตั้งเวลา : cron รายวัน เช่น  0 6 * * *  cd /path/to/site && python update_prices.py
วางผล   : prices.json ต้องอยู่โฟลเดอร์เดียวกับ index.html บนเซิร์ฟเวอร์ของคุณ
"""
import os, json, re, datetime, pathlib
from openai import OpenAI

OUT = pathlib.Path("prices.json")
MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")

# id: (ชื่อวัสดุ, หน่วย, ราคาตั้งต้นในหน้าเว็บ) ราคาตั้งต้นใช้เป็นกรอบตรวจ 0.5x-2x
MAT = {
    "conc":  ("คอนกรีตผสมเสร็จ 240 ksc", "ลบ.ม.", 2467),
    "rebar": ("เหล็กเส้นเสริมคอนกรีต SD40T 10 มม.", "กก.", 22.7),
    "steel": ("เหล็กรูปพรรณโครงหลังคา", "กก.", 30.1),
    "cem":   ("ปูนซีเมนต์ปอร์ตแลนด์ ถุง 50 กก.", "ถุง", 134),
    "sand":  ("ทรายก่อสร้าง", "ลบ.ม.", 420),
    "aac":   ("อิฐมวลเบา หนา 7.5 ซม.", "ก้อน", 30),
    "roof":  ("กระเบื้องหลังคาลอนคู่", "ตร.ม.", 310),
    "tile":  ("กระเบื้องปูพื้น/ผนัง 60x60", "ตร.ม.", 420),
    "gyp":   ("ฝ้ายิปซัมพร้อมโครงเคร่า", "ตร.ม.", 330),
    "paint": ("สีทาอาคาร แกลลอน", "แกลลอน", 1350),
    "wire":  ("สายไฟ THW 2.5 ตร.มม.", "ม.", 16),
    "pvc":   ("ท่อ PVC ชั้น 8.5", "ม.", 62),
}

today = datetime.date.today().isoformat()
lines = "\n".join(f'- {k}: {n} (บาทต่อ{u})' for k, (n, u, _) in MAT.items())
prompt = f"""วันนี้ {today} ค้นเว็บหาราคาวัสดุก่อสร้างในไทยล่าสุดของรายการต่อไปนี้ (ราคาไม่รวม VAT)
ใช้แหล่งที่ตรวจสอบได้ เช่น ระบบราคาวัสดุก่อสร้างของกระทรวงพาณิชย์/สนค. ผู้ผลิต หรือร้านค้าวัสดุรายใหญ่
{lines}
ตอบเป็น JSON อย่างเดียว รูปแบบ {{"id": {{"price": ตัวเลข, "source": "URL หน้าที่พบราคา", "date": "YYYY-MM-DD ของราคานั้น"}}}}
ถ้าหาแหล่งที่ยืนยันราคาไม่ได้ ให้ใส่ null ห้ามเดาหรือประมาณเอง"""

api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
if not api_key:
    raise SystemExit("ไม่พบ OPENROUTER_API_KEY — ตรวจชื่อ secret ใน GitHub Actions")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=api_key,
)

# เปิดการค้นเว็บของ OpenRouter ผ่าน web plugin (ใช้ได้กับทุกโมเดล)
r = client.chat.completions.create(
    model=MODEL,
    max_tokens=2000,messages=[{"role": "user", "content": prompt}],
    extra_body={"plugins": [{"id": "web", "max_results": 10}]},
)

msg = r.choices[0].message
text = msg.content or ""
m = re.search(r"\{.*\}", text, re.S)
try:
    data = json.loads(m.group(0)) if m else {}
except json.JSONDecodeError:
    data = {}

# URL ที่ OpenRouter ค้นเจอจริง (ใช้ตรวจว่าโมเดลไม่ได้แต่ง source ขึ้นเอง)
cited = set()
for a in (getattr(msg, "annotations", None) or []):
    a = a if isinstance(a, dict) else a.model_dump()
    u = (a.get("url_citation") or {}).get("url")
    if u:
        cited.add(u.rstrip("/"))

old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
items = dict(old.get("items", {}))  # ค่าเดิมคงไว้ถ้ารอบนี้ไม่ผ่านการตรวจ
for k, (_, _, base) in MAT.items():
    x = data.get(k)
    if not isinstance(x, dict):
        continue
    try:
        p = float(x["price"])
    except (KeyError, TypeError, ValueError):
        continue
    src = str(x.get("source", ""))
    src_ok = src.startswith("http") and (not cited or src.rstrip("/") in cited)
    if src_ok and base * 0.5 <= p <= base * 2:
        items[k] = {"price": p, "source": src, "date": x.get("date", today)}
    else:
        print("ข้าม", k, x)

OUT.write_text(json.dumps({"updated": today, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
print("บันทึก", len(items), "รายการ ->", OUT)

#!/usr/bin/env python3
"""อัปเดต prices.json อัตโนมัติ: ให้ LLM (ผ่าน OpenRouter) ค้นเว็บหาราคาล่าสุด แล้วตรวจความสมเหตุสมผลก่อนบันทึก

ติดตั้ง : pip install openai
ตั้งค่า  : export OPENROUTER_API_KEY=sk-or-...
          export OPENROUTER_MODEL=apodex/apodex-1.1-mini:free   # ไม่บังคับ
รัน     : python update_prices.py
"""
import os, sys, json, re, time, datetime, pathlib
from openai import OpenAI, APIStatusError, APIConnectionError

OUT = pathlib.Path("prices.json")
MODEL = os.environ.get("OPENROUTER_MODEL", "apodex/apodex-1.1-mini:free")
# โมเดลสำรอง (คั่นด้วย comma) ถ้าตัวหลักติด rate limit หรือล่ม เช่น "provider/model-b:free"
FALLBACKS = [m.strip() for m in os.environ.get("OPENROUTER_FALLBACKS", "").split(",") if m.strip()]

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
ตอบเป็น JSON อย่างเดียว ห้ามมีข้อความอื่นหรือ ``` ครอบ รูปแบบ {{"id": {{"price": ตัวเลข, "source": "URL หน้าที่พบราคา", "date": "YYYY-MM-DD ของราคานั้น"}}}}
ถ้าหาแหล่งที่ยืนยันราคาไม่ได้ ให้ใส่ null ห้ามเดาหรือประมาณเอง"""

api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
if not api_key:
    raise SystemExit("ไม่พบ OPENROUTER_API_KEY — ตรวจชื่อ secret ใน GitHub Actions")

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


def warn(msg):
    # รูปแบบนี้ทำให้ GitHub Actions แสดงเป็นคำเตือนสีเหลือง ไม่ทำให้งานล้ม
    print(f"::warning::{msg}")


extra = {
    "plugins": [{"id": "web", "max_results": 5}],  # ลดจำนวนผลค้นเพื่อประหยัดเครดิต
    "reasoning": {"exclude": True},                # ไม่ส่งส่วน "คิด" กลับมา ให้ content สะอาด
}
if FALLBACKS:
    extra["models"] = FALLBACKS

r = None
for attempt in range(3):
    try:
        r = client.chat.completions.create(
            model=MODEL,
            max_tokens=8000,  # โมเดล reasoning ใช้ token คิดก่อนตอบ ถ้าน้อยไปคำตอบจะว่าง
            messages=[{"role": "user", "content": prompt}],
            extra_body=extra,
        )
        break
    except APIStatusError as e:
        if e.status_code == 429 and attempt < 2:
            wait = 30 * (attempt + 1)
            print(f"ติด rate limit รอ {wait} วินาทีแล้วลองใหม่")
            time.sleep(wait)
            continue
        warn(f"OpenRouter error {e.status_code}: ใช้ราคาเดิมต่อ")
        print(e)
        break
    except APIConnectionError as e:
        warn(f"เชื่อมต่อ OpenRouter ไม่ได้: {e}")
        break

if r is None:
    sys.exit(0)  # ไม่แตะ prices.json ใช้ราคาเดิม

msg = r.choices[0].message
text = msg.content or ""
m = re.search(r"\{.*\}", text, re.S)
try:
    data = json.loads(m.group(0)) if m else {}
except json.JSONDecodeError:
    data = {}
if not data:
    warn("โมเดลไม่ได้ตอบเป็น JSON ที่อ่านได้ ใช้ราคาเดิมต่อ")
    print(text[:500])

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

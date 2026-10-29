# check_dt.py  (run: python check_dt.py)
import requests
B = "http://127.0.0.1:8000/api/v1"
dts = set()
p = 1
while True:
    r = requests.get(f"{B}/distribution-transformers", params={"page": p}).json()
    dts |= {d["code"] for d in r["data"]}
    if p * r["pageSize"] >= r["total"]:
        break
    p += 1

meters, p = [], 1
while True:
    r = requests.get(f"{B}/meters", params={"q": "", "page": p}).json()
    meters += r["data"]
    if p * r["pageSize"] >= r["total"]:
        break
    p += 1

orphans = [m["meterId"] for m in meters if m["dtCode"] not in dts]
print(len(meters), "meters;", len(dts), "transformers;", len(orphans), "meters with unknown dtCode")
print("sample orphans:", orphans[:5])
print("null dtCode:", sum(1 for m in meters if not m["dtCode"]))
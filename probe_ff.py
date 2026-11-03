import urllib.request, os
os.makedirs("data", exist_ok=True)
for url in ("https://www.forexfactory.com/news", "https://www.forexfactory.com/news?more=1"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept-Language": "en-US"})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
        open("data/probe_ff.html", "w").write(html[:400000]); print(url, "OK", len(html)); break
    except Exception as e:
        print(url, "ERROR", e); open("data/probe_ff.html", "w").write(f"ERROR {e}")

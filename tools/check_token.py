# Проверка токена hh.ru: читает .env, дозаписывает HH_ACCESS_TOKEN, тестирует /vacancies
# Секреты нигде не печатаются.
import json, os, re, sys, urllib.request, urllib.parse

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.environ.get("LOCALAPPDATA", "") + "\\Temp"
ENV_PATH = os.path.join(PROJECT, ".env")

def parse_env(path):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env

env = parse_env(ENV_PATH)

# 1. Токен из уже скачанного ответа (если есть) или запрашиваем заново
token = env.get("HH_ACCESS_TOKEN", "")
tok_file = os.path.join(SCRATCH, "hh_token.json")
if not token and os.path.exists(tok_file):
    d = json.load(open(tok_file, encoding="utf-8"))
    token = d.get("access_token", "")
    if token:
        print("Токен найден в сохранённом ответе, expires_in=" + str(d.get("expires_in")))

if not token:
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": env["HH_CLIENT_ID"],
        "client_secret": env["HH_CLIENT_SECRET"],
    }).encode()
    req = urllib.request.Request("https://api.hh.ru/token", data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    token = d.get("access_token", "")
    print("Токен запрошен заново, expires_in=" + str(d.get("expires_in")))

if not token:
    print("Токен НЕ получен!"); sys.exit(1)

# 2. Дозаписать в .env (без дублей) + закавычить HH_USER_AGENT
lines = open(ENV_PATH, encoding="utf-8").read().rstrip().splitlines()
out = []
seen = set()
for line in lines:
    if "=" in line:
        k, v = line.split("=", 1)
        k = k.strip()
        if k == "HH_USER_AGENT" and not (v.strip()[:1] in "\"'"):
            line = k + '="' + v.strip() + '"'
        if k == "HH_ACCESS_TOKEN":
            line = "HH_ACCESS_TOKEN=" + token
        seen.add(k)
    out.append(line)
if "HH_ACCESS_TOKEN" not in seen:
    out.append("HH_ACCESS_TOKEN=" + token)
open(ENV_PATH, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(".env обновлён: HH_ACCESS_TOKEN добавлен, HH_USER_AGENT закавычен")

# 3. Тест: поиск вакансий
ua = env.get("HH_USER_AGENT", "HH_agent_nlp/1.0")
url = "https://api.hh.ru/vacancies?" + urllib.parse.urlencode({"text": "python", "area": 1, "per_page": 3})
req = urllib.request.Request(url, headers={
    "Authorization": "Bearer " + token,
    "HH-User-Agent": ua,
})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    print("ПОИСК ВАКАНСИЙ: HTTP", r.status, "| всего найдено:", d.get("found"))
    for it in d.get("items", [])[:3]:
        sal = it.get("salary")
        s = f"{sal['from']}-{sal['to']} {sal['currency']}" if sal else "без зарплаты"
        print("  -", it["name"][:55], "|", it["employer"]["name"][:30], "|", s)
except urllib.error.HTTPError as e:
    body = e.read().decode(errors="replace")[:300]
    print("ОШИБКА /vacancies: HTTP", e.code, body)

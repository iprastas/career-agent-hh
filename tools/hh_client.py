# -*- coding: utf-8 -*-
"""
Тонкий клиент hh.ru API — ТОЛЬКО GET (чтение публичных данных).

Правила проекта:
  * никаких POST/PUT/DELETE — ничего не отправляем на hh (ни откликов, ни писем, ни черновиков);
  * персональные данные не запрашиваем: эндпоинты /resumes, /negotiations, /chats, зарплатный банк
    с application-токеном и так возвращают 403 (проверено 30.10.2026);
  * обязательный заголовок HH-User-Agent, ретраи на 429/5xx, пауза между запросами.

Использование:
    from tools.hh_client import HHClient
    hh = HHClient()
    data = hh.get("vacancies", text="c# разработчик", area=1, per_page=5)
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ALLOWED_METHODS = {"GET"}  # белый список: запись на hh запрещена проектом


def load_env(path=None):
    env = {}
    p = path or os.path.join(PROJECT, ".env")
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


class HHError(RuntimeError):
    pass


class HHClient:
    BASE = "https://api.hh.ru/"

    def __init__(self, token=None, user_agent=None, delay=0.7, timeout=45, retries=4):
        env = load_env()
        self.token = token or env.get("HH_ACCESS_TOKEN", "")
        self.user_agent = user_agent or env.get("HH_USER_AGENT", "hh_agent_nlp/1.0")
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last_call = 0.0
        if not self.token:
            raise HHError("HH_ACCESS_TOKEN не найден в .env")

    def _throttle(self):
        wait = self.delay - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)

    def get(self, path, **params):
        """GET-запрос. params=None значения отбрасываются, списки кодируются повторно."""
        if "GET" not in _ALLOWED_METHODS:  # страховка от будущих правок
            raise HHError("метод не в белом списке")
        clean = {k: v for k, v in params.items() if v is not None}
        url = self.BASE + path.lstrip("/")
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)

        last_err = None
        for attempt in range(self.retries):
            self._throttle()
            req = urllib.request.Request(url, headers={
                "Authorization": "Bearer " + self.token,
                "HH-User-Agent": self.user_agent,
            })
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    self._last_call = time.time()
                    return json.load(r)
            except urllib.error.HTTPError as e:
                self._last_call = time.time()
                body = e.read().decode(errors="replace")[:300]
                if e.code in (429, 500, 502, 503):
                    wait = 30 * (attempt + 1)
                    last_err = HHError(f"HTTP {e.code}: {body}")
                    time.sleep(wait)
                    continue
                raise HHError(f"HTTP {e.code} {url}: {body}")
            except (TimeoutError, urllib.error.URLError) as e:
                last_err = e
                time.sleep(10 * (attempt + 1))
        raise HHError(f"исчерпаны повторы {url}: {last_err}")

    # ---- удобные обёртки над GET-методами ----
    def search_vacancies(self, **params):
        return self.get("vacancies", **params)

    def vacancy(self, vacancy_id):
        return self.get(f"vacancies/{vacancy_id}")

    def related(self, vacancy_id, per_page=10):
        return self.get(f"vacancies/{vacancy_id}/related_vacancies", per_page=per_page)

    def similar(self, vacancy_id, per_page=10):
        return self.get(f"vacancies/{vacancy_id}/similar_vacancies", per_page=per_page)

    def dictionaries(self):
        return self.get("dictionaries")

    def employer(self, employer_id):
        return self.get(f"employers/{employer_id}")

    def skill_ids(self, text):
        return self.get("suggests/skill_set", text=text)

    def skills(self, ids):
        return self.get("skills", id=list(ids))


if __name__ == "__main__":
    hh = HHClient()
    d = hh.search_vacancies(text="c# разработчик", area=1, order_by="publication_time", per_page=3)
    print("найдено:", d.get("found"))
    for it in d.get("items", []):
        sal = (it.get("salary") or {})
        print(f"  {it['name']} | {it['employer']['name']} | "
              f"{sal.get('from')}-{sal.get('to')} {sal.get('currency', '')} | {it['published_at']}")
    det = hh.vacancy(d["items"][0]["id"])
    print("key_skills:", [s["name"] for s in det.get("key_skills") or []][:8])
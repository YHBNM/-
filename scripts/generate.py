#!/usr/bin/env python3
"""
HelloDaily 自动生成脚本 v2
单次搜索 + 程序分类，保证多样性，min API 调用
"""
import json
import os
import random
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path


REPO = Path("/opt/HelloDaily")
CONTENT_DIR = REPO / "content"
README = REPO / "README.md"

# GitHub Token 获取缓存
_GITHUB_TOKEN = None


def get_token():
    global _GITHUB_TOKEN
    if _GITHUB_TOKEN:
        return _GITHUB_TOKEN

    # 1. 环境变量优先
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token:
        _GITHUB_TOKEN = env_token.strip()
        return _GITHUB_TOKEN

    # 1b. 本机部署路径（HelloDaily 落地机）
    for cand in (Path("/root/.secrets/github_pat"),):
        if cand.is_file():
            try:
                token = cand.read_text(encoding="utf-8").strip()
                if token:
                    _GITHUB_TOKEN = token
                    return _GITHUB_TOKEN
            except Exception:
                pass

    # 2. 从 ~/.hermes/secrets/github_pat 读取
    pat_file = Path.home() / ".hermes" / "secrets" / "github_pat"
    if pat_file.is_file():
        try:
            token = pat_file.read_text(encoding="utf-8").strip()
            if token:
                _GITHUB_TOKEN = token
                return _GITHUB_TOKEN
        except Exception:
            pass

    # 3. 从 gh CLI 凭据读取
    try:
        out, code = run(["gh", "auth", "token"])
        if code == 0 and out.startswith("gh"):
            _GITHUB_TOKEN = out.strip()
            return _GITHUB_TOKEN
    except Exception:
        pass

    # 4. 历史兼容兜底：git remote 提取
    try:
        out, _ = run(["git", "-C", str(REPO), "remote", "-v"])
        for line in out.split("\n"):
            if "github.com" in line and "fetch" in line:
                m = re.search(r"https://([^@]+)@github\.com", line)
                if m:
                    _GITHUB_TOKEN = m.group(1)
                    return _GITHUB_TOKEN
    except Exception:
        pass
    return None


RE_CHINESE = re.compile(r"[\u4e00-\u9fff]")

MEANINGLESS_DESCS = {
    "a", "an", "the", "none", "no description", "project", "test",
    "demo", "example", "just for fun", "learning", "tutorial",
    "my first", "practice", "nothing", "update", "fix",
}

# 定向主题查询：每类直接映射到一组 GitHub topic 搜索，不再靠描述猜分类
CATEGORY_QUERIES = [
    ("🟠 Cloudflare/边缘", ["topic:cloudflare", "topic:workers", "topic:serverless"]),
    ("🏠 自建服务", ["topic:self-hosted", "topic:homelab", "topic:docker"]),
    ("🌐 网络与穿透", ["topic:proxy", "topic:tunnel", "topic:wireguard", "topic:vpn"]),
    ("🤖 安卓逆向", ["topic:frida", "topic:xposed", "topic:smali",
                    "android+reverse+in:description", "apk+decompiler+in:name,description"]),
    ("🔍 逆向与安全", ["topic:reverse-engineering", "topic:security-tools", "topic:pentest"]),
    ("💻 CLI 与终端", ["topic:cli", "topic:tui", "topic:command-line"]),
    ("🛠 开发工具", ["topic:developer-tools", "topic:automation", "topic:http-client"]),
]
PER_CAT = 4          # 每类最终收录条数
SEARCH_PER_QUERY = 15  # 每个 topic 查询取多少候选

# AI 相关关键词（避免过度推荐）
AI_BLOCKLIST = {"llm", "gpt", "chatgpt", "openai", "langchain", "rag", "vector-db",
                "fine-tune", "prompt", "diffusion", "stable-diffusion", "qwen", "llama",
                "mistral", "gemini", "claude", "copilot", "codex"}


def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), -1


def translate(text):
    if not text or len(text) < 5:
        return text
    if RE_CHINESE.search(text):
        return text
    try:
        q = urllib.parse.quote(text[:500])
        # clients5 dict-chrome-ex 端点：dc IP 上 gtx 会 429，这个还能用
        url = f"https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl=zh-CN&q={q}"
        out, code = run(["curl", "-s", "-A", "Mozilla/5.0", url], timeout=10)
        if code == 0 and out:
            data = json.loads(out)
            if isinstance(data, list) and data and isinstance(data[0], str):
                result = "".join(data)
                if result:
                    return result
            elif isinstance(data, list) and data and isinstance(data[0], list):
                result = "".join(x[0] for x in data[0] if x and x[0])
                if result:
                    return result
    except Exception:
        pass
    return text


def is_valid_project(r):
    desc = (r.get("description") or "").strip().lower()
    name = r.get("full_name", "")
    repo = name.split("/")[-1].lower()
    stars = r.get("stargazers_count", 0)

    if stars < 100 or stars > 60000:
        return False
    if repo in ("awesome", "awesome-list") or desc.startswith("a curated list") or desc.startswith("curated list"):
        return False
    if len(desc) < 8:
        return False
    if any(kw in desc for kw in MEANINGLESS_DESCS) and len(desc) < 20:
        return False
    return True


def classify_project(r):
    """根据描述和 topics 分类"""
    desc = (r.get("description") or "").lower()
    topics = [t.lower() for t in r.get("topics", [])]
    lang = (r.get("language") or "").lower()
    text = f"{desc} {' '.join(topics)} {lang}"

    # AI 检测 - 如果太多 AI 特征，定为 AI 类但不完全排除
    ai_hits = sum(1 for kw in AI_BLOCKLIST if kw in text)
    if ai_hits >= 3:
        return None  # 纯 AI 项目跳过

    # 匹配分类关键词
    scores = []
    for cat_name, keywords in CATEGORY_KEYWORDS:
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores.append((score, cat_name))

    if scores:
        scores.sort(reverse=True)
        return scores[0][1]

    # 按语言兜底
    lang_cat_map = {
        "python": "🔧 数据处理",
        "javascript": "🌐 Web 前端",
        "typescript": "🌐 Web 前端",
        "go": "💻 命令行神器",
        "rust": "💻 命令行神器",
        "c": "🛠 开发工具",
        "c++": "🛠 开发工具",
        "java": "📱 桌面/移动",
        "kotlin": "📱 桌面/移动",
        "swift": "📱 桌面/移动",
        "dart": "📱 桌面/移动",
        "ruby": "⚡ 效率提升",
        "shell": "💻 命令行神器",
        "lua": "🎮 游戏娱乐",
    }
    return lang_cat_map.get(lang, None)


def get_next_number():
    files = list(CONTENT_DIR.glob("HelloDaily*.md"))
    nums = [int(f.stem.replace("HelloDaily", "")) for f in files if re.match(r"HelloDaily\d+\.md", f.name)]
    return max(nums) + 1 if nums else 1


def get_previous_projects(count=2):
    files = sorted(CONTENT_DIR.glob("HelloDaily*.md"))
    prev = set()
    for f in files[-count:]:
        content = f.read_text(encoding="utf-8")
        for m in re.finditer(r'\*\*\[([^\]]+)\]\([^)]+\)', content):
            prev.add(m.group(1))
    return prev


def _search(q: str, per_page: int = 15):
    """一次 GitHub 搜索，返回 repo 列表（失败返回空）。"""
    token = get_token()
    headers = ["-H", f"Authorization: token {token}"] if token else []
    url = (f"https://api.github.com/search/repositories?q={urllib.parse.quote(q)}"
           f"&sort=stars&order=desc&per_page={per_page}")
    out, code = run(["curl", "-s"] + headers + [url], timeout=25)
    if code != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    if "message" in data:
        print(f"    ⚠️ {data['message'][:60]}")
        return []
    return data.get("items", [])


def fetch_repos():
    """按 6 个定向主题逐类搜索，去重后每类取 PER_CAT 条。"""
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    seen, result = set(), []

    for cat, queries in CATEGORY_QUERIES:
        bucket = []
        for q in queries:
            items = _search(f"{q} stars:>100 pushed:>{since}", SEARCH_PER_QUERY)
            for r in items:
                name = r["full_name"]
                if name in seen or not is_valid_project(r):
                    continue
                seen.add(name)
                bucket.append(r)
        random.shuffle(bucket)
        picked = bucket[:PER_CAT]
        print(f"  {cat}: 候选 {len(bucket)} → 收录 {len(picked)}")
        if picked:
            result.append((cat, picked))

    random.shuffle(result)
    return result


def format_periodical(category_repos, num):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append(f"# 《HelloDaily》第 {num:03d} 期")
    lines.append("> 用心发现 GitHub 上有趣的项目，不止于热门分类。")
    lines.append("")

    for cat_name, repos in category_repos:
        if not repos:
            continue
        lines.append(f"## {cat_name}")
        lines.append("")

        for i, r in enumerate(repos[:PER_CAT], 1):
            name = r["full_name"]
            url = r["html_url"]
            stars = r["stargazers_count"]
            desc = (r.get("description") or "").strip()
            lang = r.get("language") or ""

            cn_desc = translate(desc) if desc else "暂无简介"
            if len(cn_desc) > 100:
                cn_desc = cn_desc[:97] + "..."

            lang_tag = f" `{lang}`" if lang else ""
            lines.append(f"{i}. **[{name}]({url})** ⭐{format_stars(stars)}{lang_tag}")
            lines.append(f"   {cn_desc}")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"本期由 HelloDaily 自动生成 · {today}")
    return "\n".join(lines)


def format_stars(n):
    if n >= 1000:
        return f"{n/1000:.1f}k" if n % 1000 else f"{n//1000}k"
    return str(n)


def update_readme(num, cat_names=None):
    today = datetime.now().strftime("%Y-%m-%d")

    files = sorted(CONTENT_DIR.glob("HelloDaily*.md"))
    rows, row = [], []
    for f in files:
        m = re.match(r"HelloDaily(\d+)\.md", f.name)
        if m:
            link = f"[第 {int(m.group(1)):03d} 期](content/{f.name})"
            row.append(link)
            if len(row) == 5:
                rows.append(row); row = []
    if row:
        while len(row) < 5: row.append("")
        rows.append(row)

    table = "\n".join([
        "| :card_index: | :jack_o_lantern: | :beer: | :fish_cake: | :octocat: |",
        "| ------- | ----- | ------------ | ------ | --------- |"
    ] + ["| " + " | ".join(r) + " |" for r in rows])

    scope_line = ""
    if cat_names:
        short = " · ".join(cat_names[:6])
        scope_line = f"本期涵盖：{short} 等\n"

    _remote = run(["git", "remote", "get-url", "origin"], timeout=5)[0].strip()
    _slug = re.sub(r"^.*github\.com[:/]", "", _remote).removesuffix(".git")
    readme_content = f"""# HelloDaily

![GitHub stars](https://img.shields.io/github/stars/{_slug}?style=flat-square)
![GitHub license](https://img.shields.io/github/license/{_slug}?style=flat-square)
![Periodicals](https://img.shields.io/badge/期数-{num:03d}-blue?style=flat-square)

> 每周一三五自动更新，精选 GitHub 上不同领域的开源项目。

## 最新一期

📅 **[《HelloDaily》第 {num:03d} 期](content/HelloDaily{num:03d}.md)** · {today}

{scope_line}## 往期

{table}

## 关于

每周一三五自动搜索 GitHub 开源项目，通过关键词匹配将项目归类到开发工具、效率提升、视觉创意、游戏、学习资源、命令行、桌面应用、Web 前端、数据处理、有趣项目等 10 个领域，避免单一刷 AI 或运维类项目。

## 项目结构

```
HelloDaily/
├── content/              # 每期内容
├── scripts/              # 自动化脚本
├── README.md
└── LICENSE
---

本期由 HelloDaily 自动生成 · 每期覆盖不同领域
"""
    README.write_text(readme_content, encoding="utf-8")


TG_ENV = Path("/root/.secrets/tg.env")


def tg_creds():
    """读 /root/.secrets/tg.env 里的 bot token 与 chat id。"""
    if not TG_ENV.is_file():
        return None, None
    cfg = {}
    for line in TG_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
    return cfg.get("TELEGRAM_BOT_TOKEN"), cfg.get("TELEGRAM_CHAT_ID")


def build_tg_html(fp: Path, num: int, per_cat: int = 3):
    import html as _html
    lines = fp.read_text(encoding="utf-8").splitlines()
    date = datetime.now().strftime("%Y-%m-%d")
    out = [f"<b>《HelloDaily》第 {num:03d} 期</b> · {date}", ""]
    cat = None
    idx = 0
    total = 0
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("## "):
            cat = ln[3:].strip()
            idx = 0
        elif ln.startswith("1. ") or (idx and ln[:2].rstrip(". ").isdigit()):
            m = re.match(r"^(\d+)\. \*\*\[([^\]]+)\]\(([^)]+)\)\*\* ⭐([^\s`]+)(?: `([^`]*)`)?$", ln)
            if m and cat:
                total += 1
                idx += 1
                if idx <= per_cat:
                    if idx == 1:
                        out.append(f"\n<b>{cat}</b>")
                    desc = ""
                    if i + 1 < len(lines):
                        nxt = lines[i + 1].strip()
                        if nxt and not nxt.startswith("#") and not nxt.startswith("---"):
                            desc = nxt[:80]
                    entry = f'{idx}. <a href="{m.group(3)}">{_html.escape(m.group(2))}</a> ⭐{m.group(4)}'
                    if desc:
                        entry += f"\n   {_html.escape(desc)}"
                    out.append(entry)
        i += 1
    out.append("")
    out.append(f"共 {total} 个项目 · 完整一期见仓库 content/")
    return "\n".join(out)


def send_telegram(text: str) -> bool:
    import tempfile
    token, chat = tg_creds()
    if not token or not chat:
        print("⚠️ 未配置 /root/.secrets/tg.env，跳过 TG 推送")
        return False
    ok = True
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3500:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    for ch in chunks:
        payload = {"chat_id": chat, "text": ch, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(payload, tf, ensure_ascii=False)
            tmp = tf.name
        out, code = run(["curl", "-sS", "-m", "20", "-X", "POST",
                         f"https://api.telegram.org/bot{token}/sendMessage",
                         "-H", "Content-Type: application/json",
                         "--data-binary", f"@{tmp}"], timeout=30)
        os.unlink(tmp)
        try:
            resp = json.loads(out)
        except Exception:
            resp = {"ok": False, "description": out[:200]}
        if not resp.get("ok"):
            ok = False
            print(f"⚠️ TG 推送失败: {resp.get('description')}")
    if ok:
        print("✅ 已推送到 Telegram")
    return ok


def main():
    print("== HelloDaily 生成器 v2 ==")

    # 只推送某一期，不重新生成：python3 generate.py --notify-only content/HelloDaily065.md
    if len(sys.argv) >= 3 and sys.argv[1] == "--notify-only":
        target = Path(sys.argv[2])
        if not target.is_absolute():
            target = CONTENT_DIR / target.name
        m = re.search(r"(\d+)", target.name)
        num = int(m.group(1)) if m else get_next_number()
        text = build_tg_html(target, num)
        print(text[:400])
        sys.exit(0 if send_telegram(text) else 1)

    num = get_next_number()
    print(f"准备生成第 {num:03d} 期")

    print("拉取远程仓库...")
    os.chdir(REPO)
    run(["git", "pull", "--rebase"], timeout=20)

    print("获取 GitHub 项目（认证搜索 + 程序分类）...")
    repos = fetch_repos()
    valid = [(n, r) for n, r in repos if r]
    if not valid:
        print("❌ 获取项目失败，退出")
        sys.exit(1)

    total = sum(len(r) for _, r in valid)
    print(f"共 {total} 个项目，{len(valid)} 个分类")

    # 去重
    prev = get_previous_projects(2)
    deduped = [(n, [r for r in rs if r["full_name"] not in prev]) for n, rs in valid]
    deduped = [(n, rs) for n, rs in deduped if rs]

    print("翻译描述并生成内容...")
    content = format_periodical(deduped, num)
    fp = CONTENT_DIR / f"HelloDaily{num:03d}.md"
    fp.write_text(content, encoding="utf-8")
    print(f"已写入 {fp.name}")

    print("更新 README...")
    update_readme(num, [n for n, _ in deduped])

    print("推送仓库...")
    run(["git", "add", "."], timeout=10)
    commit_out, code = run(["git", "commit", "-m", f"发布：《HelloDaily》第 {num:03d} 期"], timeout=10)
    print(commit_out)
    if "nothing to commit" in commit_out.lower():
        return
    push_out, code = run(["git", "push", "origin", "main"], timeout=30)
    print(push_out)
    if code != 0:
        print("❌ Git 推送失败，请检查 GitHub 凭据或网络！")
        sys.exit(1)

    print(f"\n✅ 《HelloDaily》第 {num:03d} 期 已推送")
    remote = run(["git", "remote", "get-url", "origin"], timeout=5)[0].strip()
    base = remote[:-4] if remote.endswith(".git") else remote
    print(f"{base}/blob/main/content/HelloDaily{num:03d}.md")

    try:
        send_telegram(build_tg_html(fp, num))
    except Exception as e:
        print(f"⚠️ TG 推送异常: {e}")


if __name__ == "__main__":
    main()
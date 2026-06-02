import os
import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from html import escape

from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "8080"))
TZ = timezone(timedelta(hours=8))

PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
if PUBLIC_URL and not PUBLIC_URL.startswith("http"):
    PUBLIC_URL = "https://" + PUBLIC_URL

ALERT_USERS = "@HFDG168 @ZHTT16888"

workers = {}
away = {}
late_workers = {}
off_workers = {}
logs = {}
abnormal_logs = {}
alias_by_sender = {}

LIMITS = {
    "wc": ("上厕所", 15),
    "cy": ("抽烟", 10),
    "cf": ("吃饭", 35),
    "cq": ("出去", 10),
}

web = Flask(__name__)


def now():
    return datetime.now(TZ)


def get_name(user):
    return user.full_name or user.username or str(user.id)


def safe(text):
    return escape(str(text))


def make_name_uid(name):
    return "name:" + name


def add_log(chat_id, name, action, detail):
    logs.setdefault(chat_id, [])
    logs[chat_id].append({
        "time": now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "action": action,
        "detail": detail,
    })


def add_abnormal(chat_id, name, detail):
    abnormal_logs.setdefault(chat_id, [])
    abnormal_logs[chat_id].append({
        "time": now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "detail": detail,
    })


def get_shift(t):
    if 9 <= t.hour < 21:
        return "白班", t.replace(hour=10, minute=0, second=0, microsecond=0)
    return "夜班", t.replace(hour=22, minute=0, second=0, microsecond=0)


def parse_clock_text(raw_text, user):
    text = raw_text.strip()
    low = text.lower()
    sender_id = user.id
    sender_name = get_name(user)

    if low == "sb":
        return sender_id, sender_name, "sb", sender_id

    if low == "xb":
        uid = alias_by_sender.get(sender_id, sender_id)
        name = workers.get(uid, {}).get("name", sender_name)
        return uid, name, "xb", sender_id

    if text in ["上班", "已上班"]:
        return sender_id, sender_name, "sb", sender_id

    if text in ["下班", "已下班"]:
        uid = alias_by_sender.get(sender_id, sender_id)
        name = workers.get(uid, {}).get("name", sender_name)
        return uid, name, "xb", sender_id

    if text.endswith("已上班"):
        name = text[:-3].strip()
        if name:
            return make_name_uid(name), name, "sb", sender_id

    if text.endswith("上班"):
        name = text[:-2].strip()
        if name:
            return make_name_uid(name), name, "sb", sender_id

    if text.endswith("已下班"):
        name = text[:-3].strip()
        if name:
            return make_name_uid(name), name, "xb", sender_id

    if text.endswith("下班"):
        name = text[:-2].strip()
        if name:
            return make_name_uid(name), name, "xb", sender_id

    uid = alias_by_sender.get(sender_id, sender_id)
    name = workers.get(uid, {}).get("name", sender_name)
    return uid, name, low, sender_id


async def alert_group(context, chat_id, text):
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@web.route("/")
def home():
    return "打卡机器人运行中"


@web.route("/report/<chat_id>")
def report(chat_id):
    try:
        cid = int(chat_id)
    except Exception:
        return "错误的链接"

    worker_lines = []
    for i, info in enumerate(workers.values(), 1):
        worker_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜上班 {info['start'].strftime('%H:%M')}｜{'迟到' if info['late'] else '正常'}"
        )

    away_lines = []
    for i, info in enumerate(away.values(), 1):
        used_seconds = int((now() - info["start"]).total_seconds())
        used_minutes = (used_seconds + 59) // 60
        away_lines.append(
            f"{i}. {safe(info['name'])}｜{info['action']}｜已用 {used_minutes} 分钟｜限制 {info['limit']} 分钟"
        )

    late_lines = []
    for i, info in enumerate(late_workers.values(), 1):
        late_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜迟到 {info['minutes']} 分钟"
        )

    off_lines = []
    for i, info in enumerate(off_workers.values(), 1):
        off_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜下班 {info['time'].strftime('%H:%M')}｜工作 {info['work']}"
        )

    abnormal_lines = []
    for i, item in enumerate(abnormal_logs.get(cid, []), 1):
        abnormal_lines.append(
            f"{i}. {item['time']}｜{safe(item['name'])}｜{item['detail']}"
        )

    log_lines = []
    for item in logs.get(cid, []):
        log_lines.append(
            f"{item['time']}｜{safe(item['name'])}｜{item['action']}｜{item['detail']}"
        )

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>打卡详情</title>
        <style>
            body {{ font-family: Arial; padding: 20px; background:#f5f5f5; }}
            .box {{ background:white; padding:15px; margin-bottom:15px; border-radius:10px; }}
            h2 {{ margin-top:0; }}
            pre {{ white-space:pre-wrap; font-size:16px; }}
        </style>
    </head>
    <body>
        <h1>远程打卡详情</h1>

        <div class="box">
            <h2>当前上班人数：{len(workers)}</h2>
            <pre>{chr(10).join(worker_lines) if worker_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>当前离岗人员</h2>
            <pre>{chr(10).join(away_lines) if away_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>异常行为记录</h2>
            <pre>{chr(10).join(abnormal_lines) if abnormal_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>今日迟到人员</h2>
            <pre>{chr(10).join(late_lines) if late_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>今日下班人员</h2>
            <pre>{chr(10).join(off_lines) if off_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>全部打卡记录</h2>
            <pre>{chr(10).join(log_lines) if log_lines else "暂无"}</pre>
        </div>
    </body>
    </html>
    """


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await alert_group(
        context,
        update.message.chat_id,
        "打卡机器人已启动\n\n"
        "正常打卡不会提示。\n"
        "只有迟到、离岗超时等异常才会提醒主管。"
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    raw_text = update.message.text.strip()
    user = update.message.from_user
    chat_id = update.message.chat_id
    t = now()

    uid, name, text, sender_id = parse_clock_text(raw_text, user)

    if text == "sb":
        if uid in workers:
            return

        shift, late_time = get_shift(t)
        is_late = t > late_time
        late_minutes = int((t - late_time).total_seconds() // 60) if is_late else 0

        workers[uid] = {
            "name": name,
            "start": t,
            "shift": shift,
            "late": is_late,
        }

        alias_by_sender[sender_id] = uid

        if is_late:
            late_workers[uid] = {
                "name": name,
                "shift": shift,
                "minutes": late_minutes,
            }

        add_log(chat_id, name, "上班", f"{shift}｜{t.strftime('%H:%M')}｜{'迟到' if is_late else '正常'}")

        if is_late:
            await alert_group(
                context,
                chat_id,
                f"⚠️ 上班迟到，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"班次：{shift}\n"
                f"打卡时间：{t.strftime('%H:%M')}\n"
                f"迟到：{late_minutes}分钟\n\n"
                f"{ALERT_USERS}",
            )
        return

    if text == "xb":
        if uid not in workers:
            add_abnormal(chat_id, name, "未上班直接下班")
            return

        info = workers.pop(uid)
        away.pop(uid, None)

        work_time = t - info["start"]
        hours = int(work_time.total_seconds() // 3600)
        minutes = int((work_time.total_seconds() % 3600) // 60)

        off_workers[uid] = {
            "name": name,
            "shift": info["shift"],
            "time": t,
            "work": f"{hours}小时{minutes}分钟",
        }

        add_log(chat_id, name, "下班", f"{info['shift']}｜{t.strftime('%H:%M')}｜{hours}小时{minutes}分钟")
        return

    if text in LIMITS:
        action, limit = LIMITS[text]

        if uid not in workers:
            add_abnormal(chat_id, name, f"未上班直接{action}")
            return

        token = str(uuid.uuid4())

        away[uid] = {
            "token": token,
            "name": name,
            "action": action,
            "start": t,
            "limit": limit,
            "chat_id": chat_id,
        }

        add_log(chat_id, name, action, f"开始｜限制 {limit} 分钟")

        async def check_timeout(user_id, check_token, limit_minutes):
            await asyncio.sleep(limit_minutes * 60)

            info = away.get(user_id)

            if not info:
                return

            if info.get("token") != check_token:
                return

            used_seconds = int((now() - info["start"]).total_seconds())
            limit_seconds = info["limit"] * 60
            used_minutes = (used_seconds + 59) // 60

            if used_seconds <= limit_seconds:
                return

            await alert_group(
                context,
                info["chat_id"],
                f"⚠️ 离岗超时，请处理\n\n"
                f"员工：{safe(info['name'])}\n"
                f"项目：{info['action']}\n"
                f"限制时间：{info['limit']}分钟\n"
                f"当前已用：{used_minutes}分钟\n"
                f"状态：超时未归\n\n"
                f"{ALERT_USERS}",
            )

        asyncio.create_task(check_timeout(uid, token, limit))
        return

    if text == "1":
        if uid not in away:
            return

        info = away.pop(uid)

        used_seconds = int((t - info["start"]).total_seconds())
        limit_seconds = info["limit"] * 60
        used_minutes = (used_seconds + 59) // 60

        is_over = used_seconds > limit_seconds
        over_minutes = max(1, used_minutes - info["limit"]) if is_over else 0

        status = "正常" if not is_over else f"超时 {over_minutes} 分钟"

        add_log(chat_id, name, f"{info['action']}返回", f"用时 {used_minutes} 分钟｜{status}")

        if is_over:
            await alert_group(
                context,
                chat_id,
                f"⚠️ 离岗返回异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"项目：{info['action']}\n"
                f"限制时间：{info['limit']}分钟\n"
                f"实际用时：{used_minutes}分钟\n"
                f"超时：{over_minutes}分钟\n\n"
                f"{ALERT_USERS}",
            )
        return

    if text in ["rs", "/rs", "人数", "查看"]:
        if not PUBLIC_URL:
            await alert_group(context, chat_id, "请先设置 PUBLIC_URL")
            return

        await alert_group(
            context,
            chat_id,
            f'当前上班人数：{len(workers)}\n'
            f'员工名单：<a href="{PUBLIC_URL}/report/{chat_id}">详细查看</a>',
        )
        return


def run_web():
    web.run(host="0.0.0.0", port=PORT)


threading.Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle))
app.run_polling()
